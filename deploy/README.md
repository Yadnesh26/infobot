# Deploying InfoBot to AWS EC2

```
git push to main
   -> GitHub Actions: tests -> build image -> push to ECR (private registry)
   -> AWS SSM tells the EC2 instance to pull it and restart
   -> instance: Caddy (HTTPS, port 443)  ->  the bot (Docker, port 8000, not exposed)
   -> health check fails? the previous version is put back automatically
```

One small instance runs two containers. There is no SSH port; you reach the server through AWS
Session Manager in the browser. GitHub holds **no AWS keys and no bot secrets**: it signs in to
AWS with a short-lived token (OIDC), and the bot's secrets live only on the server.

What is tested already (locally, before any of this touched AWS): the image builds, runs as a
non-root user with a read-only filesystem, passes its health check, answers Meta's webhook
handshake, rejects unsigned posts, and runs ffmpeg through the bot's own code; the 305 unit tests
pass inside the image's Linux Python 3.12; Caddy proxies only `/webhook`, `/health`, `/trending`;
the rollback script was run against stand-in commands; the workflows, the shell script and the
CloudFormation template pass `actionlint`, `shellcheck` and `cfn-lint`. **Not** tested: anything
that needs your AWS account or GitHub repository, so expect to fix small things on the first run.

## One-time setup

### 1. Put the code on GitHub

The deploy workflow runs on the `main` branch (your local branch is `master`).

```bash
git branch -M main
# create an empty repository on github.com first (private is fine), then:
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Your `.env` and `deploy/app.env` are git-ignored; check `git status` shows neither before you push.
The first push starts the `deploy` workflow. **It will fail at the AWS step until step 5 is done.
That is expected.**

### 2. Create the AWS resources

In the AWS console, pick a region (**Asia Pacific (Mumbai) ap-south-1** is the obvious one for Indian
users), open **CloudFormation -> Create stack -> With new resources**, upload
`deploy/aws/infobot-stack.yaml`, and fill in:

| Parameter | What to enter |
|---|---|
| GitHubRepo | `<you>/<repo>` exactly as on GitHub |
| InstanceType | `t3.small` (default). `t3.micro` also works but is slow on long videos |
| DomainName | a domain you own (for example `bot.example.com`), or **leave empty** to get a free `<ip>.sslip.io` address |
| CreateGitHubOidcProvider | `true`, unless the account already has a GitHub OIDC provider (then the stack fails with "already exists": re-create it with `false`) |

Tick the box that says it may create IAM resources, create the stack, and wait about 5 minutes.
The **Outputs** tab then shows everything you need: `WebhookUrl`, `InstanceId`, `EcrRepositoryName`,
`GitHubRoleArn`, `Region`, `PublicIp`.

(It needs a default VPC, which new AWS accounts have. Command-line equivalent:
`aws cloudformation deploy --template-file deploy/aws/infobot-stack.yaml --stack-name infobot --capabilities CAPABILITY_NAMED_IAM --parameter-overrides GitHubRepo=<you>/<repo>`.)

If you set a domain: add a DNS **A record** for it pointing at `PublicIp`. Caddy gets the certificate
by itself once the name resolves. (Until it does, HTTPS will not work.)

### 3. Give the server the bot's secrets (once)

On your computer, build the secrets file. It contains only the settings the app reads (so not your
Supabase database password or secret key):

```bash
python scripts/make_app_env.py        # writes deploy/app.env and lists variable NAMES, never values
```

Then in the AWS console: **EC2 -> Instances -> infobot -> Connect -> Session Manager -> Connect**
(if the button is greyed out the instance is still registering; wait a few minutes). In that shell:

```bash
sudo nano /opt/infobot/app.env        # paste the whole contents of deploy/app.env, save
sudo chmod 600 /opt/infobot/app.env
sudo wc -l /opt/infobot/app.env       # should match the number of lines you pasted
```

Delete your local `deploy/app.env` afterwards. Keep `PHONE_HASH_SALT` identical to the value you have
used so far: a different salt makes every stored hash (users, rate limits, language choices) stop matching.

### 4. Tell GitHub where to deploy

Repository **Settings -> Secrets and variables -> Actions -> Variables** (the *Variables* tab, not
Secrets). Add four repository variables from the stack outputs:

| Variable | Value |
|---|---|
| `AWS_ROLE_ARN` | `GitHubRoleArn` |
| `AWS_REGION` | `Region` |
| `INSTANCE_ID` | `InstanceId` |
| `ECR_REPOSITORY` | `EcrRepositoryName` (it is `infobot`) |

Optional: **Settings -> Environments -> production -> Required reviewers** adds a click-to-approve
before every deploy. (The AWS role only trusts a job that runs in an environment named `production`;
GitHub creates it the first time the workflow uses it.)

### 5. First deploy

**Actions -> deploy -> Run workflow** (or push a commit to `main`). The first run takes about 5-10
minutes (building, pushing, and the instance pulling a ~900 MB image). The job log ends with the
instance's own output; a healthy deploy prints the running containers.

Then check it from anywhere:

```bash
curl https://<your host>/health          # {"status":"ok"}
```

### 6. Point WhatsApp at it

Meta developer dashboard -> your app -> WhatsApp -> Configuration -> Webhook:

- **Callback URL:** the `WebhookUrl` output
- **Verify token:** the `WA_VERIFY_TOKEN` value from your `.env`
- Verify and save, and make sure the `messages` field is subscribed.

Now stop the local `uvicorn` and `ngrok` (Meta only delivers to one URL, but nothing else should
compete for the same Gemini quota). Send the bot a claim from your phone to confirm, for example
"Humans use only 10 percent of their brain."

## Day to day

| I want to... | Do this |
|---|---|
| Ship a change | push to `main`; the workflow tests, builds and deploys |
| Watch the bot | Session Manager, then `sudo docker logs -f infobot-app` |
| See container state | `cd /opt/infobot && sudo docker compose ps` |
| Change a secret or setting | `sudo nano /opt/infobot/app.env`, then `cd /opt/infobot && sudo docker compose up -d --force-recreate app` |
| Roll back | push a revert commit. (A deploy that fails its health check already restores the previous image by itself.) |
| Re-run a failed deploy | Actions -> deploy -> Re-run |
| Stop paying | delete the CloudFormation stack (ECR images are removed with it) |

A deploy replaces the running container: replies already being written finish first (the bot gets up
to 45 s to wind down), and messages that arrive during the few seconds of swap are retried by Meta and
de-duplicated by message id. A hard crash or power loss can still drop a reply in flight (there is no
job queue yet).

## Cost (approximate, check the AWS pricing calculator for your region)

| Item | About |
|---|---|
| t3.small, on-demand, 24/7 | $15-17 a month (t3.micro is about half) |
| Public IPv4 address (AWS charges for every one) | $3.65 a month |
| 20 GB disk, registry storage, traffic at this scale | $2-3 a month |
| **Total** | **roughly $20-25 a month** for t3.small |

Whether the free tier or starter credits cover this depends on when the AWS account was created; look
at **Billing -> Free tier**. The model APIs (Gemini, Tavily, ...) are billed separately: measured on
Gemini's paid Flash-Lite rates, a fresh message costs roughly a tenth to a third of a US cent, and a
claim answered from the cache costs almost nothing.

## Security notes

- Open ports: 80 and 443 only. No SSH. IMDSv2 is required, the disk is encrypted.
- The webhook is public by design; every POST must carry Meta's valid signature or it gets a 403.
  `/trending` is also public (cached claims and verdicts). FastAPI's `/docs` is blocked by the proxy.
- The bot container is non-root, has a read-only filesystem (only `/tmp` is writable) and no Linux
  capabilities.
- The GitHub role can only push to this one registry and run commands on this one instance, and only
  for jobs in this repository's `production` environment.
- Secrets sit in `/opt/infobot/app.env` on the instance disk; whoever can open a Session Manager shell
  on it can read them, so keep that AWS permission tight. A step up is AWS Systems Manager Parameter
  Store, read at deploy time.
- Actions are pinned to major versions (`@v4`). For stricter supply-chain control pin them to full commit SHAs.
