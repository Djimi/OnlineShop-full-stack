# Resume Prompt — Step 1.4b

To continue from step 1.4b, paste the following into a new opencode session:

```
Continue from step 1.4b in plans/AUTOMATIC-BUILDS-AND-DEPLOY/01_MVP_DEPLOY.md.

RDS instance `onlineshop-db` was created in step 1.4a:
- Engine: PostgreSQL 18.4
- Instance: db.t4g.micro, 20 GB, eu-north-1
- Initial database: `auth`
- Security group: `onlineshop-db-sg`
- Public access: Yes (temporary)
- Master credentials are in AWS Secrets Manager

🔴 BEFORE WE PROCEED — copy the RDS endpoint:
   AWS Console → RDS → Databases → `onlineshop-db` → Connectivity & security tab
   → Copy "Endpoint" (looks like: onlineshop-db.xxxxx.eu-north-1.rds.amazonaws.com)

Guide me through 1.4b step by step.
```
