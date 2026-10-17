import sys, paramiko

HOSTS = ["192.168.18.181", "192.168.18.182"]
USER = "orangepi"
PWD = "orangepi"

def connect():
    last = None
    for h in HOSTS:
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            cli.connect(h, username=USER, password=PWD, timeout=8)
            print(f"[connected via {h}]")
            return cli
        except Exception as e:
            last = e
            cli.close()
    raise SystemExit(f"all hosts unreachable: {last}")

def main():
    cli = connect()
    for cmd in sys.argv[1:]:
        stdin, stdout, stderr = cli.exec_command(cmd, timeout=60)
        out = stdout.read().decode("utf-8", "replace").strip()
        err = stderr.read().decode("utf-8", "replace").strip()
        print(f"$ {cmd}")
        if out:
            print(out)
        if err:
            print("[stderr]", err)
        print("---")
    cli.close()

if __name__ == "__main__":
    main()
