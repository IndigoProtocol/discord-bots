# Indigo Discord Bots

[![Python - >=3.10](https://img.shields.io/badge/Python->=3.10-2ea44f?logo=python)](https://python.org/)

## How to run

```shell
sudo apt install python3-jsonschema
WEBHOOK_URL='https://discord.com/api/webhooks/…' python3 cdp.py
```

```shell
WEBHOOK_URL='https://discord.com/api/webhooks/…' python3 liquidations.py
```

## Bot ideas

- Vote results, final and on-demand partial
- Vote feed
- Look up INDY rewards based on address/handle
	- Input: interaction (slash command)
	- Output: ephemeral message
	- PKH lookup would need to be built out for this, can be best-guess based on StakeKeyHash

## Discord docs

- [Permissions](https://discord.com/developers/docs/topics/permissions)
- [Webhook](https://discord.com/developers/docs/resources/webhook#execute-webhook)
