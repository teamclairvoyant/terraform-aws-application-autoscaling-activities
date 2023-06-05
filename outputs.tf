output "app-auto-scaling-log-group-name" {
  value = aws_cloudwatch_log_group.app-autoscaling-activity-log-group.name
}

output "app-auto-scaling-lambda-function-arn" {
  value = module.lambda_function.lambda_function_arn
}

output "app-auto-scaling-lambda-trigger-cloudwatch_event_rule_id" {
  value = module.lambda-cloudwatch-trigger.cloudwatch_event_rule_id
}

output "app-auto-scaling-dynamodb" {
  value = module.dynamodb_table.dynamodb_table_id
}