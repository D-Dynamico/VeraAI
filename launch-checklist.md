# Launch checklist

The submitted URL is a cloudflared quick tunnel: its hostname is created with the
process and dies with it, and a restart issues a **different** one that nobody has.
There is no recovery from a mid-window drop, so everything here is preventive.

`tools/serve.ps1` already verifies the bot end to end and refuses to print a URL it
has not probed five times. This file covers what the script cannot: the machine, the
clock, and what to do when something goes wrong anyway.

Total commitment is **~105 minutes**, not 60 — warmup at T-15, score report at T0+90.

---

## T-60 — the machine

- [ ] **Power: never sleep.** In an admin PowerShell:
      ```powershell
      powercfg /change standby-timeout-ac 0
      powercfg /change monitor-timeout-ac 0
      powercfg /change hibernate-timeout-ac 0
      powercfg /change disk-timeout-ac 0
      ```
      Laptop on mains, not battery — the `-dc` timeouts are separate and still apply
      if the charger comes out.
- [ ] **Windows Update paused.** Settings → Windows Update → Pause updates (5 weeks).
      A forced restart is the single likeliest way to lose the window.
- [ ] **Ethernet if there is a cable.** Wi-Fi roaming between APs drops connections;
      the tunnel reconnects, but the judge does not retry.
- [ ] **Nothing else launched.** No Docker Desktop (it stopped unprompted twice during
      one build session), no VPN, no large downloads.
- [ ] `powercfg /requests` — confirm nothing is holding an unexpected wake lock, and
      that nothing you rely on is missing one.

## T-30 — the software

- [ ] `git status` clean, on `main`, in sync with `origin`.
- [ ] `.env` has a `GEMINI_API_KEY`. Confirm it still has quota:
      `PYTHONIOENCODING=utf-8 python tools/local_harness.py --llm 2` should report
      `fell back 0`. An exhausted key is **not** a failure — the bot serves templates
      and stays inside every rule — but it is a materially weaker submission, and you
      want to know which one you are shipping.
- [ ] Port 8123 free. `.\tools\serve.ps1 -Stop` if anything is left from a rehearsal.
- [ ] `PYTHONIOENCODING=utf-8 python -m pytest -q` — 7 pass.

## T-20 — bring it up

- [ ] `.\tools\serve.ps1`
      It runs preflight, starts uvicorn, raises the tunnel, then verifies healthz and
      metadata through the **public** hostname and samples healthz five more times. If
      any of that fails it exits non-zero and prints no URL. Trust that: a URL it
      refuses to print is a URL that would have failed warmup.
- [ ] Take the URL from **`logs/public-url.txt`**, not the console. The script reports
      through `Write-Host`, which bypasses the output stream — `$u = .\tools\serve.ps1`
      returns nothing.
- [ ] Submit it.
- [ ] **Do not touch either process again.** No restart, no code edit, no
      `POST /v1/teardown` — teardown clears the context store and the judge does not
      re-push. Teardown is for between rehearsals only.

## During the window — every ~10 minutes

- [ ] `.\tools\serve.ps1 -Check`

  Probe the **public URL, never the process.** On 2026-08-29 a quick tunnel stopped
  resolving while `cloudflared` sat there alive and retrying a hostname that no longer
  existed in DNS. A process watchdog would have reported healthy the whole time.

  `-Check` is read-only and safe to run mid-window. Expect `uptime_seconds` to climb
  monotonically; a reset means uvicorn restarted and the context store is empty.

## If it breaks mid-window

The judge polls healthz every 60s and **three consecutive misses disqualifies the
slot**, so the downtime budget is under three minutes. That is shorter than a restart
plus re-verification, so there is no scenario where restarting quietly saves you.

| What `-Check` says | What it means | Do |
|---|---|---|
| public healthz fails, local healthz fine | the tunnel hostname is gone — the DNS failure above | Restart is the only option and it yields a **new** URL. Only worth doing if the submission can still be edited; otherwise the slot is lost either way, so try it. |
| local healthz fails too | uvicorn died | `.\tools\serve.ps1 -Stop` then `.\tools\serve.ps1`, and re-submit the new URL. The context store is empty regardless, so warmup must re-run. |
| `uptime_seconds` reset | uvicorn restarted under you | Contexts are gone. Nothing to do but let the judge re-push, if it will. |
| healthz fine, ticks returning `[]` | not a fault | Restraint is a scored output. Empty ticks are expected and rewarded. |

## After

- [ ] Score report at roughly T0+90.
- [ ] `.\tools\serve.ps1 -Stop`.
- [ ] Copy `logs/` somewhere before the next run clears it — the launcher deletes the
      log files on every bring-up.
