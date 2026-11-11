# Step 1: Home (an always-on host)

**Goal:** a machine that stays awake, can be reached over SSH, starts Jarvis at boot,
and restarts it if it crashes. Hermes isn't installed yet, so the service runs in
*placeholder mode*: it only writes a heartbeat. That lets you prove auto-start works
before adding anything else.

**Cost:** free. No accounts needed.

## Pick a machine

| | Old laptop + Ubuntu | Mac mini / MacBook |
|-|-|-|
| Best for | Cheapest; the sturdiest choice for 24/7 | Needed later if you want iMessage |
| Starts at boot without login | Yes | Only with auto-login (not possible if FileVault is on) |
| Power draw | 10–30 W | 5–10 W (Mac mini) |

Either works. Any machine with 8 GB of RAM is plenty, because the model runs in the cloud.
If you're using an old laptop, a fresh install of **Ubuntu 24.04 LTS** (Desktop or Server) is the easiest path.

Before you start:
- Plug it into power and use **Ethernet** if you can.
- In your router's settings, give it a **fixed (reserved) IP** so you can always find it.
- Use a strong password for your user account.

---

## A. Linux (Ubuntu/Debian)

Open **Terminal** on the machine and run these one at a time:

```bash
# 1. Get git and download this project
sudo apt update && sudo apt install -y git
git clone https://github.com/shiven-goel/Jarvis.git ~/jarvis
cd ~/jarvis

# 2. Run the setup (it will ask for your password)
./host/linux/setup.sh
```

If the repo is private, `git clone` will ask for your GitHub username and a
**personal access token** (not your password). Create one at
GitHub → Settings → Developer settings → Fine-grained tokens, with read-only access to this repo.

What the script does:
1. Installs `curl`, `git`, `openssh-server`, `ufw`, and turns on automatic security updates
2. Turns off sleep and suspend, and makes closing the laptop lid do nothing
3. Turns on SSH and a firewall that only lets SSH in
4. Creates `~/.jarvis/` (only your user can read it)
5. Installs the `jarvis` systemd service and turns on *linger*, so it starts at boot even before anyone logs in

## B. macOS

Open **Terminal** and run:

```bash
git clone https://github.com/shiven-goel/Jarvis.git ~/jarvis   # first time may prompt to install developer tools
cd ~/jarvis
./host/macos/setup.sh
```

Then do two things by hand:
1. **System Settings → Users & Groups → Automatically log in as →** your user.
   (If it's greyed out, FileVault is on. Either turn FileVault off, or accept that you'll log in once after each reboot.)
2. **MacBook only:** keep the lid open, or connect power and an external display, so it doesn't sleep.

What the script does: disables system sleep, restarts automatically after a power cut (Mac mini),
turns on the firewall and SSH (Remote Login), creates `~/.jarvis/`, and installs a launchd
agent (`com.jarvis.agent`) that starts at login and restarts if it stops.

---

## ✅ Verify it worked

**1. Health check**

```bash
~/jarvis/host/healthcheck.sh
```

You want every line to say `PASS` and the last line to say `All checks passed.`
(If it says there's no heartbeat file yet, wait 30 seconds and run it again.)

**2. It restarts after a crash.** Kill it on purpose:

```bash
pkill -f run-jarvis.sh
sleep 15
~/jarvis/host/healthcheck.sh      # should be all PASS again
```

**3. It survives a reboot**

```bash
sudo reboot
```

After it comes back up, **don't log in on the machine itself**. From another computer, connect over SSH
(`ssh yourname@<machine-ip>`) and run `~/jarvis/host/healthcheck.sh`. All PASS means Step 1 is done.
(On a Mac without auto-login, log in first.)

**4. See the log**

- Linux: `journalctl --user -u jarvis -f`
- macOS: `tail -f ~/.jarvis/logs/jarvis.log`

You should see `Jarvis host is up (placeholder mode...)`.

## Useful commands

| | Linux | macOS |
|-|-|-|
| Status | `systemctl --user status jarvis` | `launchctl print gui/$(id -u)/com.jarvis.agent \| head` |
| Restart | `systemctl --user restart jarvis` | `launchctl kickstart -k gui/$(id -u)/com.jarvis.agent` |
| Stop | `systemctl --user stop jarvis` | `launchctl bootout gui/$(id -u)/com.jarvis.agent` |
| Remove | `~/jarvis/host/uninstall.sh` | `~/jarvis/host/uninstall.sh` |

## Security notes

- Jarvis runs as **you**, not root, and the systemd unit sets `NoNewPrivileges`, so the agent can't use sudo.
- `~/.jarvis` is `chmod 700` and `host.env` is `chmod 600`.
- The only open inbound port is SSH. Later, the dashboard will listen on `localhost` only.
  To reach it from your phone we'll use something like Tailscale (free), not open router ports.
- Recommended: switch SSH to key-only login (`ssh-copy-id` from your laptop, then set
  `PasswordAuthentication no` in `/etc/ssh/sshd_config`). Only do this after you've confirmed key login works.

## How Step 2 plugs in

Step 2 installs Hermes and adds a single line to `~/.jarvis/host.env`:

```bash
JARVIS_CMD="hermes gateway"
```

Then the same service runs and supervises Hermes, with nothing else to change.
