from netmiko import ConnectHandler

device = {
    "device_type": "huawei_telnet",
    "host": "192.168.56.10",
    "username": "admin",
    "password": "huawei",  # ou ton mot de passe actuel
    "port": 23,
    "timeout": 30,
    "conn_timeout": 30,
    "global_delay_factor": 3,       # ← laisse plus de temps
    "session_log": "netmiko_session.log",  # ← active le log brut pour voir exactement ce qui est reçu
}

try:
    conn = ConnectHandler(**device)
    print(conn.send_command("display version", expect_string=r"[>\]]", read_timeout=20))
    conn.disconnect()
except Exception as e:
    print(f"ERREUR : {type(e).__name__}: {e}")