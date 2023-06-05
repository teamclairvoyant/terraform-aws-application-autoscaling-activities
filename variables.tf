variable "ecs_cluster_arn" {
  type        = string
  description = "Provide the ECS cluster ARN"
}

variable "trigger_schedule_cron" {
  type        = string
  description = "Provide the cron expression for the Event Bridge rule"
  default     = "cron(0 10 * * ? *)"
}

variable "aws_region" {
  type        = string
  description = "Provide the aws region"
  default     = "us-west-2"

}