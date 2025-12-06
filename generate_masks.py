import yaml, os

params = [
    "etx","rssi","pfi",
    "re","bdi","qo","qlr","hc","si","tv","pc",
    "wr","str"
]

base = {p: False for p in params}

os.makedirs("testbed/masks", exist_ok=True)

for p in params:
    d = base.copy()
    d[p] = True   # only this parameter ON
    path = f"testbed/masks/mask-{p}.yaml"
    with open(path, "w") as f:
        yaml.dump(d, f, sort_keys=False)
    print("Created", path)
