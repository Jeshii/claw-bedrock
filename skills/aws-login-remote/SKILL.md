---
name: aws-login-remote
description: >
  Simplified AWS authentication for remote development using `aws login --remote`.
  Use this skill when users need to authenticate to AWS from a remote server
  without browser access, or when models confuse `aws login` with `aws sso login`.
---

# AWS Login Remote - Skill

## Overview

`aws login --remote` is a newer AWS CLI feature (v2.32.0+) that simplifies
authentication for remote development servers. It uses OAuth 2.0 with PKCE to
securely deliver temporary credentials from a device with browser access to
a remote machine without browser access.

**Key difference from `aws sso login`:**
- `aws sso login` requires pre-configured SSO profiles in `~/.aws/config`
- `aws login` (and `aws login --remote`) uses your AWS Management Console
  sign-in method directly - no profile configuration needed

## When to Use This Skill

Use `aws login --remote` when:
- Working on a remote server (EC2, Cloud9, Codespaces, etc.) without browser access
- User mentions "remote login", "headless auth", or "no browser"
- User is confused about why `aws sso login` isn't working
- User needs to authenticate from a development container or remote environment

## Command Usage

### Basic Remote Login Flow

1. **On the remote machine** (without browser):
   ```bash
   aws login --remote
   ```

2. **The CLI will output**:
   - A message that browser will not be opened automatically
   - A long URL to visit on your local machine with browser access
   - A prompt: `Enter the authorization code displayed in your browser:`
   - Example output:
     ```
     Browser will not be automatically opened.
     Please visit the following URL:

     https://ap-northeast-1.signin.aws.amazon.com/v1/authorize?response_type=code&client_id=...

     Enter the authorization code displayed in your browser:
     ```

3. **On your local machine** (with browser):
   - Open the provided URL
   - Sign in with your AWS Console credentials
   - After signing in, the browser will display an **authorization code**
   - Copy the authorization code from the browser
   - Paste it into the management UI's code input field (or directly into the terminal if running interactively)

4. **Back on the remote machine**:
   - After submitting the code, CLI shows success message
   - Example: `Updated profile default to use arn:aws:iam::342330301416:user/bedrock-mantle-openai20b credentials.`
   - Credentials are cached and auto-rotated every 15 minutes
   - Valid up to the IAM principal's session duration (max 12 hours)

### Using Profiles

Configure and use named profiles with `--remote`:
```bash
# First time setup for a profile
aws login --remote --profile myprofile

# Use the profile for commands
aws sts get-caller-identity --profile myprofile
```

## Important Notes

### Authorization Code Prompt
When using `--remote`, AWS will not open a browser automatically. Instead:
1. The CLI displays a URL to open on a device with browser access
2. After signing in on that device, the browser displays an **authorization code**
3. Copy and paste that code back into the remote terminal when prompted
4. The prompt will say: `Enter the authorization code displayed in your browser:`

### Credential Rotation
- Temporary credentials auto-rotate every 15 minutes
- After session duration expires (max 12 hours), re-run `aws login --remote`
- No long-term access keys are created or stored

### Region Selection
If no default Region is set, the CLI will prompt for one on first login.
The selection is saved for future use.

## Troubleshooting

### "Unknown options: --remote"
Upgrade AWS CLI to v2.32.0 or later:
```bash
aws --version  # Check current version
# Follow AWS CLI upgrade instructions for your OS
```

### Browser Opens on Remote Machine
You're not using `--remote` flag. Use `aws login --remote` explicitly for
headless environments.

### Authorization Code Not Accepted
- Ensure you're copying the entire code from the browser (no hyphens, it's a long string)
- Codes expire quickly - re-run `aws login --remote` if expired
- Make sure you're entering the code in the same terminal that initiated the login

### Permission Denied
The `aws login` command requires IAM permissions:
- `signin:AuthorizeOAuth2Access`
- `signin:CreateOAuth2Token`

Request the `SignInLocalDevelopmentAccess` managed policy from your admin.

## Comparison with Other Auth Methods

| Method | Browser Required | Profile Config | Credential Type |
|--------|-----------------|----------------|-----------------|
| `aws login` | On same machine | None needed | Temporary, auto-rotating |
| `aws login --remote` | On different machine | None needed | Temporary, auto-rotating |
| `aws sso login` | On same machine | SSO profile required | Temporary, refreshable |
| `aws configure` | Never | Access keys | Long-term (not recommended) |

## References

- [AWS Blog Post](https://aws.amazon.com/blogs/security/simplified-developer-access-to-aws-with-aws-login/)
- [AWS CLI Documentation](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)
- [AWS SDK Support](https://docs.aws.amazon.com/sdkref/latest/guide/access-login.html)
