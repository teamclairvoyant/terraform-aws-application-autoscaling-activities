locals {
  filename = "lambda.zip"
  label    = "app-autoscaling-activity"
  all_cluster_services_resource = replace(var.ecs_cluster_arn,":cluster",":service")
 }

module "dynamodb_table" {
  source  = "terraform-aws-modules/dynamodb-table/aws"
  version = "3.1.2"

  name         = "${module.this.id}-latest-${local.label}"
  hash_key     = "ServiceName"
  billing_mode = "PAY_PER_REQUEST"
  create_table = true

  attributes = [
    {
      name = "ServiceName"
      type = "S"
    }
  ]

  tags = module.this.tags
}

resource "aws_cloudwatch_log_group" "app-autoscaling-activity-log-group" {
  name              = "/aws/ecs/${module.this.id}/scaling.log"
  retention_in_days = 90
  tags              = module.this.tags
}


data "aws_iam_policy_document" "applicationAutoScalingActivitiesLambdaPolicy" {
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:Scan",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem"
    ]
    resources = [
      module.dynamodb_table.dynamodb_table_arn
    ]
  }
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      aws_cloudwatch_log_group.app-autoscaling-activity-log-group.arn,
      "${aws_cloudwatch_log_group.app-autoscaling-activity-log-group.arn}:log-stream:*"
    ]
  }
  statement {
    effect = "Allow"
    actions = [
      "ecs:ListServices"
    ]
    resources = [
      "*"
    ]
  }
    statement {
    effect = "Allow"
    actions = [
      "ecs:DescribeServices",
    ]
    resources = [
      "${local.all_cluster_services_resource}/*"
    ]
  }
  statement {
    effect = "Allow"
    actions = [ "application-autoscaling:DescribeScalingActivities" ]
    resources = [ "*" ]
  }
}


module "lambda_function" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "2.34.1"

  function_name                     = "${module.this.id}-${local.label}-ingester"
  description                       = "This Lambda ingests the Application Auto Scaling activities from a given ecs cluster"
  handler                           = "index.lambda_handler"
  runtime                           = "python3.8"
  create_function                   = true
  attach_policy_json                = true
  policy_json                       = data.aws_iam_policy_document.applicationAutoScalingActivitiesLambdaPolicy.json
  source_path                       = "${path.module}/src/lambda"
  create_package                    = true
  publish                           = true
  cloudwatch_logs_retention_in_days = 30
  timeout                           = 300

  environment_variables = {
    "ecs_cluster_arn" : var.ecs_cluster_arn,
    "application_autoscaling_activities_loggroup" : aws_cloudwatch_log_group.app-autoscaling-activity-log-group.name,
    "dynamo_db_table" : module.dynamodb_table.dynamodb_table_id

  }

  tags = module.this.tags
}

module "lambda-cloudwatch-trigger" {
  source  = "infrablocks/lambda-cloudwatch-events-trigger/aws"
  version = "0.3.0"

  region                = var.aws_region
  component             = "${module.lambda_function.lambda_function_name}-trigger"
  deployment_identifier = module.this.environment

  lambda_arn                 = module.lambda_function.lambda_function_arn
  lambda_function_name       = module.lambda_function.lambda_function_name
  lambda_schedule_expression = var.trigger_schedule_cron
}

resource "aws_cloudwatch_query_definition" "app-auto-scaling-activities" {
  name = "${module.this.id}-latest-${local.label}"

  log_group_names = [
    aws_cloudwatch_log_group.app-autoscaling-activity-log-group.name
  ]

  query_string = <<EOF
fields @timestamp, @message
| parse Cause /monitor alarm (?<alarm>.*) in state ALARM triggered policy (?<policy>.*)/
| parse ResourceId /service\/.*\/(?<service>.*)/
| display ActivityId, service, policy, alarm
| stats count(*) by service, policy, alarm
| sort @timestamp desc
| limit 20
EOF
}

