#!/usr/bin/env bash
set -euo pipefail

echo "=== OnlineShop Staging Environment Setup ==="
echo "This script creates a staging environment in the existing onlineshop-cluster."
echo "Staging services are scaled to 0 by default (on-demand)."
echo ""

AWS_ARGS="--profile dpm-profile --region eu-north-1"
CLUSTER="onlineshop-cluster"
VPC_ID="vpc-06eeb0bc47ecdbd61"
ECS_SG="sg-0b209104a6b15b157"
ALB_INGRESS_SG="sg-0b5427a6a3bf31c29"
PUBLIC_SUBNETS=$(aws ec2 describe-subnets $AWS_ARGS \
  --query 'Subnets[*].SubnetId' --output text)
EXECUTION_ROLE="arn:aws:iam::799111666795:role/ecsTaskExecutionRole"

# --- Step 1: Create staging databases on RDS ---
echo "=== Step 1: Staging databases ==="
RDS_ENDPOINT="onlineshop-postgres-db.cf2gikqaqh9f.eu-north-1.rds.amazonaws.com"
echo "Create databases 'auth_staging' and 'items_staging' on RDS manually:"
echo "  docker run --rm -e PGPASSWORD=<db-admin-password> postgres:18-alpine psql -h $RDS_ENDPOINT -U dbadmin -c \"CREATE DATABASE auth_staging;\""
echo "  docker run --rm -e PGPASSWORD=<db-admin-password> postgres:18-alpine psql -h $RDS_ENDPOINT -U dbadmin -c \"CREATE DATABASE items_staging;\""
echo ""
echo "Then apply schemas:"
echo "  docker run --rm -e PGPASSWORD=<db-admin-password> postgres:18-alpine psql -h $RDS_ENDPOINT -U dbadmin -d auth_staging -f Auth/init-db/01-schema.sql"
echo "  docker run --rm -e PGPASSWORD=<db-admin-password> postgres:18-alpine psql -h $RDS_ENDPOINT -U dbadmin -d items_staging -f Items/init-db/01-schema.sql"
echo ""

# --- Step 2: Create staging service accounts ---
echo "=== Step 2: Staging service accounts ==="
echo "Run in psql as dbadmin:"
echo "  CREATE ROLE auth_app_staging WITH LOGIN PASSWORD '<auth-staging-app-password>';"
echo "  GRANT CONNECT ON DATABASE auth_staging TO auth_app_staging;"
echo "  \\c auth_staging"
echo "  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO auth_app_staging;"
echo "  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO auth_app_staging;"
echo ""
echo "  CREATE ROLE items_app_staging WITH LOGIN PASSWORD '<items-staging-app-password>';"
echo "  GRANT CONNECT ON DATABASE items_staging TO items_app_staging;"
echo "  \\c items_staging"
echo "  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO items_app_staging;"
echo "  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO items_app_staging;"
echo ""

# --- Step 3: Create staging Secrets Manager entries ---
echo "=== Step 3: Staging Secrets Manager entries ==="
echo "Run:"
echo "  aws secretsmanager create-secret $AWS_ARGS --name onlineshop/auth/db-staging --secret-string '{\"username\":\"auth_app_staging\",\"password\":\"<auth-staging-app-password>\",\"host\":\"onlineshop-postgres-db.cf2gikqaqh9f.eu-north-1.rds.amazonaws.com\",\"port\":\"5432\",\"dbname\":\"auth_staging\"}'"
echo "  aws secretsmanager create-secret $AWS_ARGS --name onlineshop/items/db-staging --secret-string '{\"username\":\"items_app_staging\",\"password\":\"<items-staging-app-password>\",\"host\":\"onlineshop-postgres-db.cf2gikqaqh9f.eu-north-1.rds.amazonaws.com\",\"port\":\"5432\",\"dbname\":\"items_staging\"}'"
echo ""

# --- Step 4: Add staging secrets to ecsTaskExecutionRole ---
echo "=== Step 4: Update ecsTaskExecutionRole with staging secret access ==="
echo "Run:"
echo "  aws iam put-role-policy $AWS_ARGS --role-name ecsTaskExecutionRole --policy-name secretsmanager-read-onlineshop --policy-document '{
    \"Version\":\"2012-10-17\",
    \"Statement\":[{
      \"Effect\":\"Allow\",
      \"Action\":\"secretsmanager:GetSecretValue\",
      \"Resource\":[
        \"arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/auth/db*\",
        \"arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/items/db*\"
      ]
    }]
  }'"
echo "(Note: The db* wildcard already covers db-staging entries)"
echo ""

# --- Step 5: Create staging ALB ---
echo "=== Step 5: Staging ALB ==="
echo "Run:"
echo "  aws elbv2 create-load-balancer $AWS_ARGS \\"
echo "    --name onlineshop-staging-alb \\"
echo "    --subnets $PUBLIC_SUBNETS \\"
echo "    --security-groups $ALB_INGRESS_SG"
echo ""

echo "  aws elbv2 create-target-group $AWS_ARGS \\"
echo "    --name onlineshop-staging-tg \\"
echo "    --protocol HTTP --port 10000 --target-type ip \\"
echo "    --vpc-id $VPC_ID \\"
echo "    --health-check-path /actuator/health \\"
echo "    --health-check-interval-seconds 30 \\"
echo "    --healthy-threshold-count 2 \\"
echo "    --unhealthy-threshold-count 5"
echo ""

echo "  ALB_ARN=\$(aws elbv2 describe-load-balancers $AWS_ARGS --names onlineshop-staging-alb --query 'LoadBalancers[0].LoadBalancerArn' --output text)"
echo "  TG_ARN=\$(aws elbv2 describe-target-groups $AWS_ARGS --names onlineshop-staging-tg --query 'TargetGroups[0].TargetGroupArn' --output text)"
echo "  aws elbv2 create-listener $AWS_ARGS --load-balancer-arn \$ALB_ARN --protocol HTTP --port 80 --default-actions Type=forward,TargetGroupArn=\$TG_ARN"
echo ""

# --- Step 6: Register staging task definitions ---
echo "=== Step 6: Staging task definitions ==="
echo ""
echo "Create auth-staging task definition (adapt from onlineshop-auth, change:"
echo "  - SPRING_DATASOURCE_URL → jdbc:postgresql://.../auth_staging"
echo "  - SPRING_DATASOURCE_USERNAME → auth_app_staging"
echo "  - secrets valueFrom → onlineshop/auth/db-staging"
echo ""
echo "Create items-staging task definition (adapt from onlineshop-items, change:"
echo "  - SPRING_DATASOURCE_URL → jdbc:postgresql://.../items_staging"
echo "  - SPRING_DATASOURCE_USERNAME → items_app_staging"
echo "  - secrets valueFrom → onlineshop/items/db-staging"
echo ""
echo "Create api-gateway-staging task definition (adapt from onlineshop-api-gateway, change:"
echo "  - SPRING_APPLICATION_JSON → {\"gateway\":{\"auth\":{\"service-url\":\"http://auth-staging:9001\"},\"items\":{\"service-url\":\"http://items-staging:9000\"}}}"
echo ""

# --- Step 7: Create staging ECS services ---
echo "=== Step 7: Staging ECS services (scaled to 0) ==="
echo ""
echo "  aws ecs create-service $AWS_ARGS \\"
echo "    --cluster $CLUSTER \\"
echo "    --service-name onlineshop-auth-staging \\"
echo "    --task-definition onlineshop-auth-staging \\"
echo "    --desired-count 0 \\"
echo "    --launch-type FARGATE \\"
echo "    --platform-version LATEST \\"
echo "    --network-configuration \"awsvpcConfiguration={subnets=[$PUBLIC_SUBNETS],securityGroups=[$ECS_SG],assignPublicIp=ENABLED}\" \\"
echo "    --service-connect-configuration '{"
echo "      \"enabled\": true,"
echo "      \"namespace\": \"onlineshop.local\","
echo "      \"services\": [{"
echo "        \"portName\": \"auth-staging-port\","
echo "        \"clientAliases\": [{\"port\": 9001, \"dnsName\": \"auth-staging\"}]"
echo "      }]"
echo "    }'"
echo ""

echo "  aws ecs create-service $AWS_ARGS \\"
echo "    --cluster $CLUSTER \\"
echo "    --service-name onlineshop-items-staging \\"
echo "    --task-definition onlineshop-items-staging \\"
echo "    --desired-count 0 \\"
echo "    --launch-type FARGATE \\"
echo "    --platform-version LATEST \\"
echo "    --network-configuration \"awsvpcConfiguration={subnets=[$PUBLIC_SUBNETS],securityGroups=[$ECS_SG],assignPublicIp=ENABLED}\" \\"
echo "    --service-connect-configuration '{"
echo "      \"enabled\": true,"
echo "      \"namespace\": \"onlineshop.local\","
echo "      \"services\": [{"
echo "        \"portName\": \"items-staging-port\","
echo "        \"clientAliases\": [{\"port\": 9000, \"dnsName\": \"items-staging\"}]"
echo "      }]"
echo "    }'"
echo ""

echo "  aws ecs create-service $AWS_ARGS \\"
echo "    --cluster $CLUSTER \\"
echo "    --service-name onlineshop-api-gateway-staging \\"
echo "    --task-definition onlineshop-api-gateway-staging \\"
echo "    --desired-count 0 \\"
echo "    --launch-type FARGATE \\"
echo "    --platform-version LATEST \\"
echo "    --network-configuration \"awsvpcConfiguration={subnets=[$PUBLIC_SUBNETS],securityGroups=[$ECS_SG],assignPublicIp=ENABLED}\" \\"
echo "    --load-balancers targetGroupArn=\$TG_ARN,containerName=api-gateway,containerPort=10000 \\"
echo "    --service-connect-configuration '{"
echo "      \"enabled\": true,"
echo "      \"namespace\": \"onlineshop.local\","
echo "      \"services\": [{"
echo "        \"portName\": \"gateway-staging-port\","
echo "        \"clientAliases\": [{\"port\": 10000, \"dnsName\": \"gateway-staging\"}]"
echo "      }]"
echo "    }'"
echo ""

# --- Step 8: IAM permission updates ---
echo "=== Step 8: IAM role update for ECS deploy ==="
echo "Add these permissions to the github-actions-onlineshop role:"
echo ""
echo "  aws iam put-role-policy $AWS_ARGS --role-name github-actions-onlineshop --policy-name ecs-deploy-staging --policy-document '{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Effect\": \"Allow\",
      \"Action\": [
        \"ecs:DescribeTaskDefinition\",
        \"ecs:RegisterTaskDefinition\",
        \"ecs:UpdateService\",
        \"ecs:DescribeServices\",
        \"ecs:ListTasks\",
        \"ecs:DescribeTasks\"
      ],
      \"Resource\": \"*\"
    }]
  }'"
echo ""

echo "=== Setup script complete (dry-run mode) ==="
echo "All commands above are for manual execution. Review and run each step."
