import network_as_code, os

base = os.path.dirname(network_as_code.__file__)
qod_dir = os.path.join(base, "qod")

for root, dirs, files in os.walk(qod_dir):
    for f in files:
        if f.endswith(".py") and ("application" in f.lower() or "ipv4" in f.lower()):
            path = os.path.join(root, f)
            print(f"=== {path} ===")
            print(open(path).read())
            print()