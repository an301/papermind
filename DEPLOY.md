# Putting PaperMind online

One rented server runs everything: the website, the API, Redis, Neo4j and the
three MCP servers, with Caddy in front getting the HTTPS certificate on its
own. Readers open your link, type an access code once, and use the app. About
an hour of your time, most of it waiting for things to build.

```
reader ── https://your-domain ──▶ Caddy ──┬── /        the built frontend
                                          └── /api/*   API (4 workers) ── MCP servers, Redis, Neo4j
                                                         └── Claude (your Anthropic key)
```

## What it costs

| | |
|---|---|
| Server, 8 GB RAM (e.g. Hetzner's 8 GB plan, ~€7/month; DigitalOcean or AWS Lightsail 8 GB, ~$40–48/month) | fixed |
| Domain name (Porkbun, Namecheap, Cloudflare) | ~$10/year, optional |
| Claude, per question asked | ~$0.07–0.11 |
| Claude, each time someone opens a paper (prompt-cache warm-up) | ~$0.05 |

The Claude costs come out of your Anthropic account, so the server has guards
(all set by the setup script, all changeable in `.env`):

| setting | default | what it stops |
|---|---|---|
| `ACCESS_CODE` | generated | strangers and bots: nothing works without it |
| `QUESTIONS_PER_HOUR` | 20 | one visitor running up the bill |
| `PAPERS_PER_DAY` | 5 | one visitor filling the disk |
| `DAILY_BUDGET_USD` | 5 | everyone together: chat pauses until midnight UTC |
| `ALLOW_PAPER_DELETE` | false | one visitor emptying the shared library |

Also set a monthly limit in the Anthropic Console (Settings → Limits). That
limit is enforced by Anthropic, so it holds even if the server's own counters
are wrong.

## 1. Get a server

Create an **Ubuntu 24.04** server with **8 GB of RAM** (4 GB will run out of
memory while a paper is being added). Any provider works.

For SSH you need a key. On your Mac:

```bash
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519   # press Enter at every question
cat ~/.ssh/id_ed25519.pub                             # paste this into the provider's "SSH key" box
```

Note the server's **IP address** when it's created.

If the provider has a firewall, allow ports **22, 80 and 443** in. Nothing else
needs to be open: Redis, Neo4j and the API are only reachable from inside the server.

## 2. Point a domain at it (optional, but needed for https)

At your domain's DNS settings, add an **A record**: name `papermind` (or `@`
for the bare domain), value = the server's IP. On Cloudflare, set it to
**DNS only** (grey cloud).

You can skip this and start on the bare IP over plain http, then add the domain later.

## 3. Install

```bash
ssh root@YOUR_SERVER_IP
git clone https://github.com/an301/papermind.git /opt/papermind
cd /opt/papermind && bash infra/setup-server.sh
```

If the repo is private, clone with a read-only token instead (GitHub →
Settings → Developer settings → Fine-grained tokens → only this repo,
Contents: read-only):
`git clone https://YOUR_TOKEN@github.com/an301/papermind.git /opt/papermind`

The script asks four things:

1. your **Anthropic API key**;
2. your **domain**, e.g. `papermind.example.com` (blank for the IP);
3. an **access code** (blank generates one);
4. the **daily Claude budget** in dollars.

Then it installs Docker, adds swap, builds everything (5–10 minutes the first
time), loads the concept graph, starts PaperMind on every boot, and offers to
add eight well-known ML papers to the library. At the end it prints the URL and
the access code.

## 4. Check it

Open the URL, type the code, open a paper, ask a question. The first answer
after a restart is a few seconds slower while the paper's index loads.

Share the URL and the code with people. Everyone shares one library; each
browser gets its own reading level and "what you know".

## Day to day

The script installs a `papermind` command (it's `docker compose` for this install):

```bash
papermind ps                  # what's running
papermind logs -f api         # follow the API's log (Ctrl+C to stop)
papermind logs web            # Caddy: certificate problems show up here
papermind restart api
```

**Change a setting:** edit `/opt/papermind/.env`, then `bash infra/setup-server.sh`.

**Change the access code:** same thing; everyone is asked for the new one on their next request.

**Deploy new code:** `cd /opt/papermind && git pull && bash infra/setup-server.sh`.

**See today's Claude spend:** `papermind exec redis redis-cli get spend:$(date -u +%F)`.

## If something's wrong

| symptom | look at |
|---|---|
| the domain doesn't load, or shows a certificate error | `papermind logs web`. Usually DNS doesn't point at the server yet (it can take up to an hour), or ports 80/443 are blocked. |
| "The server didn't respond" in the app | `papermind ps`: is `api` up? Then `papermind logs api`. |
| answers say the budget is used up | the daily cap was reached; raise `DAILY_BUDGET_USD` or wait for midnight UTC |
| adding a paper fails | `papermind logs api`; arXiv sometimes rate-limits, so try again in a minute |
| the server is slow or containers restart | `free -h`; an 8 GB server with the script's swap should be enough |

## The fine-tuned Mistral writer

The server uses Claude to write answers (`WRITER_BACKEND=claude`). To use the
fine-tuned Mistral instead you need a GPU serving it (see `training/README.md`,
"Serving"), then in `.env`:

```
WRITER_BACKEND=mistral
MISTRAL_BASE_URL=https://your-gpu-endpoint/v1
MISTRAL_MODEL=papermind
```

The server itself needs no GPU; only the endpoint does. A GPU that's always on
costs $200–600/month, so for a demo a serverless GPU endpoint (which starts
when asked, with a 30–90 s first answer) is the usual choice.
