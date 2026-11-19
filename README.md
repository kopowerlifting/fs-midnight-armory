# WoW Armory Archive

The crawler has already saved the character snapshots. The layout workflow
below reads those snapshots and leaves their profile JSON and raw HTML
unchanged. The browser viewer reads local saved data; it does not try to render
Blizzard's JavaScript-dependent armory page.

## Import talent tree layouts

Exact node positions and unlock connections are not part of the armory
profile. They are saved separately under `wow_archive\talent_layouts`; this
step does not refresh or modify character snapshots.

Create a Game Data API client in the
[Blizzard Developer Portal](https://develop.battle.net/access/clients), then
set the client credentials in the current PowerShell session:

```powershell
$env:BLIZZARD_CLIENT_ID = Read-Host "Blizzard client ID"
$secret = Read-Host "Blizzard client secret" -AsSecureString
$env:BLIZZARD_CLIENT_SECRET = [System.Net.NetworkCredential]::new("", $secret).Password
.\venv\Scripts\python.exe talent_layouts.py --namespace static-us
Remove-Item Env:BLIZZARD_CLIENT_ID, Env:BLIZZARD_CLIENT_SECRET
Remove-Variable secret
```

The tool uses Blizzard's client-credentials OAuth flow and public Game Data
API only; it does not request player-account access. Layout files record the
requested namespace and API source, and are separate from the timestamped
character captures. Use a versioned `static-...` namespace when you know the
snapshot's exact game build; the armory snapshots do not themselves identify
that build. The viewer draws the class, specialization, and selected hero tree
from the API's node positions and `unlocks` links.

## View the local archive

```powershell
.\venv\Scripts\python.exe viewer.py
```

Open <http://127.0.0.1:8765/>. The default archive is the `wow_archive`
folder beside `viewer.py`, regardless of the shell's current directory. Use
`--archive-dir` only when the archive is stored elsewhere. The viewer is
read-only and binds to the local machine by default. It displays the captured
equipment, item stats, character stats, PvP ratings, professions,
specialization loadouts, and PvP talents. The character list is in a
collapsible left sidebar so the profile can use more of the window.
When available, it also serves locally archived item, talent, and portrait
images. Once layout metadata has been imported, choose a specialization and
saved loadout to see selected nodes on their patch-specific talent trees.

### Access from your local network

To share the viewer with devices on the same trusted Wi-Fi/LAN, start it with:

```powershell
.\venv\Scripts\python.exe viewer.py --host 0.0.0.0
```

Find this computer's private IPv4 address with:

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.AddressState -eq "Preferred" -and $_.IPAddress -notmatch "^(127|169\.254)\." } |
  Select-Object InterfaceAlias, IPAddress
```

On another device on that network, open `http://<computer-ip>:8765/`, using
the address shown for this computer's Wi-Fi or LAN adapter. If Windows
Firewall prompts, allow Python on **Private networks only**. The archive has
no login or access control, so anyone who can reach the viewer can read the
captured profiles and images. Keep this on a trusted network; do not enable
router port forwarding or expose it to the public internet. The default
`127.0.0.1` binding remains available for local-only use.

This is the archive/viewer foundation, not yet a build editor or gearing
progression planner. It can only preserve data exposed by the public armory
profile; private inventory, bank, and other unavailable character data cannot
be inferred from that source.
