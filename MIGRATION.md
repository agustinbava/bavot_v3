# Migrar Bavot a la laptop Ubuntu (host permanente)

Objetivo: que los 8 procesos del cron vivan en la laptop de casa
(siempre prendida) y la Mac quede libre. REGLA DE ORO: **un solo host
corre los crons a la vez** — dos hosts = journals duplicados y doble
gasto LLM en T1.

## 1. En la Mac: empaquetar

```bash
cd ~/Documents
tar czf bavot.tgz \
  --exclude='scalp-analyzer/.venv' \
  --exclude='scalp-analyzer/logs/*' \
  scalp-analyzer
scp bavot.tgz usuario@LAPTOP_IP:~
```

Incluye TODO lo importante: código, `bavot.db` (journals completos),
`.env` (keys), `bavot_tg.session` (sesión de Telegram), `config.yaml`,
`agents/`, `runs/`.

## 2. En la laptop Ubuntu: instalar

```bash
tar xzf bavot.tgz && cd scalp-analyzer
python3 --version          # necesita >=3.12 (Ubuntu 24.04 trae 3.12)
sudo apt install -y python3-venv python3-pip
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt telethon
.venv/bin/python -m pytest -q          # 25 tests deben pasar
.venv/bin/python a5_daily_trend.py --report   # smoke test con la DB real
```

Notas:
- Telegram puede pedir re-login en hardware nuevo (aviso de seguridad en
  la app). Si la sesión no anda: `rm bavot_tg.session` y
  `.venv/bin/python telegram_collector.py --login`.
- Zona horaria: `sudo timedatectl set-timezone America/Argentina/Mendoza`
  (los horarios del cron están en ART; verificar con `date`).

## 3. Cron en la laptop

```bash
sed "s|/Users/agustingimenezbava/Documents|$HOME|g" crontab.txt > crontab.ubuntu
crontab crontab.ubuntu && crontab -l
mkdir -p logs
```

## 4. Dashboard accesible desde la Mac / el teléfono

En la laptop (como servicio, sobrevive reinicios):

```bash
sudo tee /etc/systemd/system/bavot-dashboard.service > /dev/null <<UNIT
[Unit]
Description=Bavot dashboard
After=network.target
[Service]
WorkingDirectory=%h/scalp-analyzer
ExecStart=%h/scalp-analyzer/.venv/bin/python dashboard.py --host 0.0.0.0
Restart=always
User=$USER
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl enable --now bavot-dashboard
```

Después: `http://IP_DE_LA_LAPTOP:8787` desde cualquier dispositivo de la
casa. (`--host 0.0.0.0` = sólo red local; NO abrir el puerto al exterior.)

## 5. En la Mac: APAGAR los crons (crítico)

```bash
crontab -r        # borra el crontab de la Mac
pkill -f dashboard.py
```

Verificar en la laptop al día siguiente: `tail logs/cron.log` con
corridas de 21:10/21:20/21:30 y snapshots horarios frescos en el
dashboard.

## 6. Backup (nuevo deber de la laptop)

```bash
(crontab -l; echo '0 8 * * * cp ~/scalp-analyzer/bavot.db ~/bavot_backup_$(date +\%u).db') | crontab -
```

Siete backups rotativos diarios de la DB — los journals son ahora el
activo más valioso del proyecto.
