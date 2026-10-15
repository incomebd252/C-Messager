# C-Messager (Pro)

Messenger-er moto chat app. Python (Flask) diye banano, browser theke cholay (computer ar phone duitotei).

## Ki ki ache

| Feature | Details |
|---|---|
| Registration | Nam, thikana, jonmo tarikh, phone, email (optional), password. Privacy Policy te sommoti nite hoy |
| Unique ID | Prottek user ke ekta ID No dewa hoy (CM1001, CM1002 ...). ID ba nam diye khuje chat shuru kora jay |
| Chat | Prai sathe sathe message, "seen" tick (✓✓), na-pora message er count, tab er title e unread count |
| Chhobi | Chat e chhobi pathano, profile chhobi (na dile nam er prothom okkhor diye default chhobi) |
| Online status | "Online" ba "Last seen ... age" |
| Group chat | Group baniye member add / bad deya, group chhara. Group admin chhere gele onno member admin hoy |
| Message delete | Nijer pathano message muchhe fela jay |
| Profile | Nam, bio, thikana, phone, email, chhobi bodlano, password bodlano |
| Dark mode | Ekta button e Light / Dark |
| Admin panel | `/admin` e: mot user, online user, ajker notun user, sob user er tottho, search, Ban / Unban, password reset, Delete, group list, Excel download |
| Excel | Notun keu registration korle ba profile bodlale `data/users.xlsx` automatic update hoy |
| Security | Password hash kore rakha, CSRF protection, login brute-force block, chhobi re-encode, security header |

Admin panel e message er lekha dekhano hoy na (user der privacy er jonno).

## 1. Computer e chalan (test korar jonno)

```bash
pip install -r requirements.txt
python app.py
```

Browser e kholen: http://localhost:5000

Admin password thik korte `.env.example` file ta copy kore `.env` nam din, tarpor bhitore `CM_ADMIN_PASSWORD` e nijer password likhun. `.env` na thakle app chalu hobar somoy terminal e ekta temporary admin password dekhabe.

Admin panel: http://localhost:5000/admin/login

## 2. Je kono network theke use korar upay

Ekhon porjonto app shudhu apnar computer e chalche. Onno network (onno WiFi, mobile data) theke use korte hole app ti internet e public korte hobe. Duita upay:

### Upay A: Tunnel diye test (apnar computer on thakle)

Free, kono server kinte hoy na. Apnar computer e app chalu rekhe ekta "tunnel" chalan, ota ekta public HTTPS link dey.

Cloudflare Tunnel (cloudflared install korte hobe, quick tunnel e account lage na):

```bash
python app.py
# onno terminal e:
cloudflared tunnel --url http://localhost:5000
```

Terminal e `https://xxxx.trycloudflare.com` moto ekta link ashbe. Oi link jeno bondhuder pathan, tara je kono network theke dhukte parbe. Tunnel chalanor age `.env` e dun:

```
CM_HTTPS=1
CM_BEHIND_PROXY=1
```

Ngrok o same kaj kore (free account lage): `ngrok http 5000`.

Shimabodhdhota: computer bondho korle app bondho, ar quick tunnel er link prottek bar notun hoy. Eta shudhu demo / test er jonno.

### Upay B: Asol server e (24 ghonta chalu thakbe)

Ekta VPS (Ubuntu server) bhara nin (DigitalOcean, Hetzner, Vultr ityadi), ar nijer domain nam kinun. Domain er A record e server er IP dan. Tarpor server e (`chat.yourdomain.com` er jaygay nijer domain likhben):

```bash
# 1. Python ar Caddy (automatic HTTPS er jonno) install
sudo apt update && sudo apt install -y python3-venv python3-pip
# Caddy install: https://caddyserver.com/docs/install

# 2. Project folder ta server e /opt/c-messager e upload korun, tarpor:
cd /opt/c-messager
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env        # CM_ADMIN_PASSWORD, CM_HTTPS=1, CM_BEHIND_PROXY=1 thik korun
sudo chown -R www-data:www-data /opt/c-messager
```

`/etc/systemd/system/c-messager.service` file banan:

```ini
[Unit]
Description=C-Messager
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/c-messager
EnvironmentFile=/opt/c-messager/.env
ExecStart=/opt/c-messager/venv/bin/gunicorn -w 1 --threads 8 -b 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now c-messager
```

`/etc/caddy/Caddyfile` e:

```
chat.yourdomain.com {
    reverse_proxy 127.0.0.1:5000
}
```

```bash
sudo systemctl reload caddy
sudo ufw allow 80,443/tcp   # firewall use korle
```

Ekhon `https://chat.yourdomain.com` e jekono network theke dhukte parben.

### Docker diye (jodi Docker use koren)

```bash
docker build -t c-messager .
docker run -d --name c-messager -p 5000:5000 -v cm-data:/app/data --env-file .env c-messager
```

`-v cm-data:/app/data` obossoi dibben, na hole container muchle sob user ar message hariye jabe.

### Hosting platform (Render, Railway, Fly.io ityadi)

`Dockerfile` ba `Procfile` diye deploy kora jay. Ekta kotha mathay rakhun: database ar chhobi `data/` folder e thake, tai platform e persistent disk / volume `/app/data` e mount korte hobe. Persistent disk chhara deploy korle prottek restart / notun deploy e data muche jete pare. Free plan e persistent disk ache kina, ta platform er current plan dekhe nin.

## 3. Settings (`.env` file)

| Name | Kaj |
|---|---|
| `CM_ADMIN_PASSWORD` | Admin panel er password. Obossoi boro kore din |
| `CM_SECRET_KEY` | Khali rakhle app nijei `data/secret.key` toiri kore |
| `CM_HTTPS` | Internet e HTTPS diye chalale `1` |
| `CM_BEHIND_PROXY` | Caddy / Nginx / Tunnel er pichone chalale `1` |
| `CM_DATA_DIR` | Data kothay thakbe (default `./data`) |
| `CM_TZ_OFFSET_HOURS` | Excel / admin e somoy (default `6`, Bangladesh) |
| `CM_ALLOW_NAME_SEARCH` | `0` dile shudhu exact ID No diye user khoja jabe |

## 4. Backup

`data/` folder ta (database, chhobi, `users.xlsx`) niyomito copy kore rakhun.

## 5. Ei version er shimabodhdhota

- Message "prai real-time" (2 second por por check kore), WebSocket noy. Kome-majhari user er jonno thik ache.
- SQLite database + 1 ta process: hajar hajar user er jonno PostgreSQL ar WebSocket (Flask-SocketIO) e jete hobe.
- Message end-to-end encrypted noy.
- Phone / email verify (OTP) kora hoy na. Password bhule gele admin panel theke temporary password dite hoy.
- Push notification (app bondho thakle) nei.
- Sotti manusher tottho (nam, thikana, birthday, phone) rakhle desher data protection ain mene Privacy Policy nijer moto kore likhun.
