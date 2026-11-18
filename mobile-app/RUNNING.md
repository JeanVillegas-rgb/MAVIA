# Running the MAVIA mobile app on a device

The `android/` folder is a prebuilt debug build: it loads its JS from Metro at
launch and talks to the Django API over HTTP. Two things have to be reachable
from the phone — **Metro (8081)** and the **API (8000)**.

## USB path (recommended — no firewall, no Wi-Fi setup)

`mobile-app/.env` is already set for this (`http://localhost:8000/api`).

1. **Backend** — from `k:\STUDIO\mavia\backend`:
   ```
   k:\STUDIO\Milestone1-Jean\mavia\Scripts\python.exe manage.py runserver
   ```
   (default `127.0.0.1:8000` is fine here.)

2. **Metro** — from `k:\STUDIO\mavia\mobile-app`:
   ```
   npx expo start --clear
   ```
   `--clear` matters after any `.env` change or dependency add (`EXPO_PUBLIC_*`
   is inlined into the bundle).

3. **Bridge the phone's localhost to this PC** (re-run after every reconnect):
   ```
   adb reverse tcp:8081 tcp:8081
   adb reverse tcp:8000 tcp:8000
   ```

4. Launch the app (or press `r` in the Metro terminal to reload). Register /
   log in should now work.

## Wi-Fi path (no cable)

1. In `mobile-app/.env`, switch to the LAN-IP lines (commented at the bottom).
   Confirm the IP with `ipconfig` first — DHCP changes it.
2. `python manage.py runserver 0.0.0.0:8000` (bind the LAN interface).
3. `192.168.68.100` is already in `DJANGO_ALLOWED_HOSTS` (`backend/.env`); update
   it if your IP differs.
4. Allow inbound TCP 8000 (elevated PowerShell):
   ```
   netsh advfirewall firewall add rule name="MAVIA dev 8000" dir=in action=allow protocol=TCP localport=8000
   ```
   and set the Wi-Fi adapter's network profile to **Private**.
5. `npx expo start --clear --lan`, then reload.

## Native rebuild (only when a native module changes)

`expo-av` was added for the audiobook player. If the app red-screens with
*"Cannot find native module 'ExpoAV'"*, the installed APK predates it — rebuild:
```
npx expo run:android
```

## Emulator

Use `http://10.0.2.2:8000/api` (the emulator's alias for the host). That host is
also in `DJANGO_ALLOWED_HOSTS`.
