#!/usr/bin/env python3
"""
Claude agentic DAST scanner (DevSecOps PoC).

Claude actively probes a RUNNING web app (via an HTTP tool it controls) to look
for common web vulnerabilities, then records structured findings to a JSON file.
A later CI step turns each finding into a GitHub issue.

AUTHORIZED USE ONLY. This is meant to be pointed at a deliberately vulnerable
demo app (OWASP Juice Shop) that YOU own, running on localhost inside an
isolated CI sandbox. The script hard-restricts all traffic to the single target
host you pass in. Do not point it at systems you are not authorized to test.

Env / args:
  ANTHROPIC_API_KEY   (required)  your Anthropic API key
  CLAUDE_MODEL        (optional)  default: claude-sonnet-5
  --target            base URL of the app (default http://localhost:3000)
  --max-steps         tool-call budget to cap time/cost (default 40)
  --out               findings JSON output path (default findings.json)
"""

import argparse
import json
import os
import sys
from urllib.parse import urljoin, urlparse

import requests
from anthropic import Anthropic

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
BODY_TRUNCATE = 4000          # max chars of a response body handed back to Claude
REQUEST_TIMEOUT = 15

SYSTEM_PROMPT = """\
You are an authorized web application security tester performing a DAST (Dynamic
Application Security Testing) assessment. The target is a DELIBERATELY VULNERABLE
demo application that the operator owns, running locally inside an isolated CI
sandbox. You have explicit authorization to test ONLY the provided base URL.

Your goal: find real, demonstrable web vulnerabilities and report each one.

Method:
- Start by exploring: fetch the base URL, look for API endpoints, forms, login,
  search, and product/user endpoints.
- Systematically probe for the OWASP Top 10, e.g. SQL/NoSQL injection, reflected
  and stored XSS, broken authentication / weak JWT, broken access control /
  IDOR, security misconfiguration, sensitive data exposure, SSRF, and open
  redirects.
- Confirm before reporting: prefer evidence (a response that proves the issue)
  over speculation. Note when something is only "suspected".
- Use the http_request tool to interact with the app. You may only reach the
  authorized host.
- When you have confirmed or strongly suspect a vulnerability, call
  record_finding with concrete evidence. One call per distinct vulnerability.
- Be efficient with your tool-call budget. When you have covered the main
  surface area or exhausted the budget, stop and give a short summary.

Do not attempt destructive actions (no mass deletion, no DoS). Keep payloads
minimal and proof-of-concept only.
"""

TOOLS = [
    {
        "name": "http_request",
        "description": (
            "Send an HTTP request to the authorized target app and get the "
            "response back. Use a path (e.g. '/rest/products/search?q=test') "
            "which is joined to the target base URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
                },
                "path": {
                    "type": "string",
                    "description": "Path (and optional query string) on the target host.",
                },
                "headers": {
                    "type": "object",
                    "description": "Optional request headers as key/value strings.",
                    "additionalProperties": {"type": "string"},
                },
                "body": {
                    "type": "string",
                    "description": "Optional raw request body (e.g. JSON string).",
                },
            },
            "required": ["method", "path"],
        },
    },
    {
        "name": "record_finding",
        "description": "Record one confirmed or suspected vulnerability.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short finding title."},
                "severity": {
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low", "info"],
                },
                "vulnerability_type": {
                    "type": "string",
                    "description": "e.g. 'SQL Injection', 'Reflected XSS', 'IDOR'.",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["confirmed", "suspected"],
                },
                "description": {"type": "string"},
                "evidence": {
                    "type": "string",
                    "description": "Request/response snippet proving the issue.",
                },
                "remediation": {"type": "string"},
            },
            "required": ["title", "severity", "vulnerability_type", "description", "remediation"],
        },
    },
]


def make_http_tool(base_url):
    """Return an http_request handler locked to the base_url's host."""
    allowed_host = urlparse(base_url).netloc
    session = requests.Session()

    def handler(inp):
        path = inp.get("path", "/")
        url = urljoin(base_url, path)
        if urlparse(url).netloc != allowed_host:
            return {"error": f"Blocked: only {allowed_host} may be tested."}
        try:
            resp = session.request(
                method=inp.get("method", "GET"),
                url=url,
                headers=inp.get("headers") or None,
                data=inp.get("body"),
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            )
            body = resp.text or ""
            truncated = len(body) > BODY_TRUNCATE
            return {
                "status_code": resp.status_code,
                "response_headers": dict(resp.headers),
                "body": body[:BODY_TRUNCATE],
                "body_truncated": truncated,
            }
        except requests.RequestException as e:
            return {"error": str(e)}

    return handler


def run(target, max_steps, out_path):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: ANTHROPIC_API_KEY is not set.")

    client = Anthropic(api_key=api_key)
    http_handler = make_http_tool(target)
    findings = []

    messages = [{
        "role": "user",
        "content": (
            f"Begin an authorized DAST assessment of the app at {target}. "
            "Explore the surface area, probe for OWASP Top 10 issues, and record "
            "each vulnerability you find with the record_finding tool."
        ),
    }]

    for step in range(max_steps):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            # Claude is done (no more tool calls requested).
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    print(f"[claude] {block.text}")
            break

        tool_results = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            if block.name == "http_request":
                result = http_handler(block.input)
                print(f"[step {step}] {block.input.get('method')} {block.input.get('path')} "
                      f"-> {result.get('status_code', result.get('error'))}")
            elif block.name == "record_finding":
                findings.append(block.input)
                result = {"recorded": True, "count": len(findings)}
                print(f"[finding] {block.input.get('severity','?').upper()}: {block.input.get('title')}")
            else:
                result = {"error": f"unknown tool {block.name}"}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result)[:6000],
            })
        messages.append({"role": "user", "content": tool_results})
    else:
        print(f"[info] Reached max-steps budget ({max_steps}).")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"target": target, "model": MODEL, "findings": findings}, f, indent=2)
    print(f"\nWrote {len(findings)} finding(s) to {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="http://localhost:3000")
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--out", default="findings.json")
    args = ap.parse_args()
    run(args.target, args.max_steps, args.out)
