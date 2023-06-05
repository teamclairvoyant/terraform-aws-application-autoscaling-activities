import boto3
import os
import logging
from datetime import date, datetime, timezone
from itertools import groupby
import json


def lambda_handler(event, lambda_context):
    # os.environ['AWS_PROFILE'] = 'lms-nonprod'
    # os.environ['ecs_cluster_arn'] = 'xxx'
    # os.environ['application_autoscaling_activities_loggroup'] = 'yyy'
    # os.environ['dynamo_db_table'] = 'zzz'

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    current_time = datetime.now(timezone.utc)
    ecs_cluster_arn = os.getenv("ecs_cluster_arn")
    application_autoscaling_activities_loggroup = os.getenv('application_autoscaling_activities_loggroup')
    dynamo_db_table = os.getenv('dynamo_db_table')
    ecs_cluster_name = ecs_cluster_arn.split('/')[1]

    client_ecs = boto3.client('ecs')
    client_app_autoscaling = boto3.client('application-autoscaling')
    client_logs = boto3.client('logs')
    client_dynamodb = boto3.client('dynamodb')
    resource_dynamodb = boto3.resource('dynamodb')

    services = []
    service_details = []
    next_token = None

    while True:
        services_response = get_ecs_service_list(ecs_cluster_arn, client_ecs, next_token)
        services.extend(services_response['serviceArns'])
        service_details_response = client_ecs.describe_services(cluster=ecs_cluster_arn,
                                                                services=services_response['serviceArns'])
        service_details.extend(service_details_response['services'])

        if 'nextToken' in services_response:
            next_token = services_response['nextToken']
        else:
            break

    data = scan_recursive(resource_dynamodb, dynamo_db_table)
    data_dict = dict((x['ServiceName'], x) for x in data)

    service_scaling_activities = []
    last_activities = []
    for service in service_details:
        service_name = service['serviceName']
        last_processed_activity_id = data_dict[service_name]['ActivityId'] if service_name in data_dict else None
        last_activity = {
            'ServiceName': service_name,
            'ActivityId': last_processed_activity_id,
            'TimeStamp': current_time
        }
        scaling_activities = []
        next_token = None
        flag = True
        while flag:
            service_scaling_activities_response = get_app_autoscaling_activities(ecs_cluster_name,
                                                                                 client_app_autoscaling, service_name,
                                                                                 next_token)

            for scaling_activity in service_scaling_activities_response['ScalingActivities']:
                if (current_time - scaling_activity['StartTime']).days < 14:

                    if scaling_activity['ActivityId'] == last_processed_activity_id:
                        flag = False
                        break

                    service_scaling_activities.append(scaling_activity)
                    scaling_activities.append(scaling_activity)

            if 'nextToken' in service_scaling_activities_response:
                next_token = service_scaling_activities_response['nextToken']
            else:
                break

        if len(scaling_activities) > 0:
            last_activity['ActivityId'] = scaling_activities[0]['ActivityId']
            last_activity['TimeStamp'] = scaling_activities[0]['StartTime']
            last_activities.append(last_activity)

    if len(service_scaling_activities) != 0:

        sorted_service_scaling_activities = sorted(service_scaling_activities, key=lambda e: e['StartTime'].isoformat())
        groups = [list(result) for key, result in
                  groupby(sorted_service_scaling_activities, key=lambda e: e['StartTime'].date().isoformat())]

        for group in groups:

            log_events = []
            day = group[0]['StartTime'].date().isoformat()
            for activity in group:
                event = {
                    'timestamp': int(round(activity['StartTime'].timestamp() * 1000)),
                    'message': json.dumps(activity, default=json_serial)
                }
                log_events.append(event)

            log_events.sort(key=sort_activities_fun)

            try:
                client_logs.create_log_stream(
                    logGroupName=application_autoscaling_activities_loggroup,
                    logStreamName=day
                )
            except client_logs.exceptions.ResourceAlreadyExistsException:
                print(f"The log stream {day} already exists")

            response = client_logs.put_log_events(
                logGroupName=application_autoscaling_activities_loggroup,
                logStreamName=day,
                logEvents=log_events
            )
        # logger.info(response)

        for activity in last_activities:
            response = client_dynamodb.update_item(
                ExpressionAttributeNames={
                    '#A': 'ActivityId',
                    '#T': 'TimeStamp',
                },
                ExpressionAttributeValues={
                    ':a': {
                        'S': activity['ActivityId'],
                    },
                    ':t': {
                        'S': activity['TimeStamp'].isoformat(),
                    },
                },
                Key={
                    'ServiceName': {
                        'S': activity['ServiceName'],
                    },
                },
                ReturnValues='ALL_NEW',
                TableName=dynamo_db_table,
                UpdateExpression='SET #A = :a, #T = :t',
            )
        # logger.info(response)


def get_app_autoscaling_activities(ecs_cluster_name, client_app_autoscaling, service_name, next_token):
    if next_token is None:
        return client_app_autoscaling.describe_scaling_activities(
            ServiceNamespace='ecs',
            ResourceId=f'service/{ecs_cluster_name}/{service_name}',
            ScalableDimension='ecs:service:DesiredCount',
            MaxResults=50,
            IncludeNotScaledActivities=True
        )
    else:
        return client_app_autoscaling.describe_scaling_activities(
            ServiceNamespace='ecs',
            ResourceId=f'service/{ecs_cluster_name}/{service_name}',
            ScalableDimension='ecs:service:DesiredCount',
            MaxResults=50,
            IncludeNotScaledActivities=True,
            nextToken=next_token
        )


def get_ecs_service_list(ecs_cluster_arn, client_ecs, next_token):
    if next_token is None:
        return client_ecs.list_services(
            cluster=ecs_cluster_arn,
            maxResults=10,
            launchType='FARGATE'
        )
    else:
        return client_ecs.list_services(
            cluster=ecs_cluster_arn,
            maxResults=10,
            launchType='FARGATE',
            nextToken=next_token
        )


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError("Type %s not serializable" % type(obj))


def sort_activities_fun(e):
    return e['timestamp']


def scan_recursive(client_dynamodb, table_name, **kwargs):
    """
        NOTE: Anytime you are filtering by a specific equivalency attribute such as id, name 
        or date equal to ... etc., you should consider using a query not scan

        kwargs are any parameters you want to pass to the scan operation
        """
    db_table = client_dynamodb.Table(table_name)
    response = db_table.scan(**kwargs)
    if kwargs.get('Select') == "COUNT":
        return response.get('Count')
    data = response.get('Items')
    while 'LastEvaluatedKey' in response:
        response = kwargs.get('table').scan(ExclusiveStartKey=response['LastEvaluatedKey'], **kwargs)
        data.extend(response['Items'])
    return data

# lambda_handler("")
