# Scheduled MT5 -> PTIS ingest to keep ASE candle freshness from drifting stale.
#
# Read-only: pulls confirmed bars from the running MT5 terminal and writes PTIS.
# It places NO orders. The confirm token below only permits `config` to import
# under the current demo posture (EXECUTOR_MODE=demo, REAL_ORDERS_ALLOWED=false);
# it is the sanctioned acknowledgment for that posture, not a credential, and is
# already a public constant in config.py (_REAL_ORDER_CONFIRM_TOKEN).
#
# Requires the MT5 demo terminal to be running in the logged-on user's session;
# if it is not connected the ingest logs a warning and no-ops (fails closed).

$ErrorActionPreference = 'Continue'
$repo = 'C:\dev\athena-python'
Set-Location $repo

$env:ATHENA_REAL_ORDERS_CONFIRM = 'I_UNDERSTAND_REAL_ORDER_RISK'

$log = Join-Path $repo 'logs\ase_ingest_scheduled.log'
$start = Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK'
"[$start] ase mt5_live ingest start" | Out-File -FilePath $log -Append -Encoding utf8

& "$repo\.venv\Scripts\python.exe" ase_cli.py ingest --sources mt5_live --no-audit *>> $log

$code = $LASTEXITCODE
$end = Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK'
"[$end] ase mt5_live ingest exit=$code" | Out-File -FilePath $log -Append -Encoding utf8
exit $code
