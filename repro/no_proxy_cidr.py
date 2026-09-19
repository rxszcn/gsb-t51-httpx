import os

import httpx
from httpx._utils import URLPattern, get_environment_proxies

for k in list(os.environ):
    if "proxy" in k.lower():
        del os.environ[k]

os.environ["ALL_PROXY"] = "http://proxy.invalid:1234"
os.environ["NO_PROXY"] = "192.168.0.0/16,10.0.0.0/8"

env = get_environment_proxies()
print("get_environment_proxies() ->", env)

for key in ("all://192.168.0.0/16", "all://10.0.0.0/8"):
    patt = URLPattern(key)
    print(f"\npattern {key}: host={patt.host!r} port={patt.port!r} "
          f"regex={patt.host_regex.pattern if patt.host_regex else None}")
    for target in ["http://192.168.0.0", "http://192.168.0.5", "http://192.168.255.254",
                   "http://10.0.0.7", "http://10.1.2.3", "http://192.168.1.1"]:
        print(f"   matches({target}) -> {patt.matches(httpx.URL(target))}")

print("\n--- end-to-end: which transport does the client pick? ---")
with httpx.Client(trust_env=True) as client:
    for target in ["http://192.168.0.5", "http://10.0.0.7", "http://192.168.0.0",
                   "http://example.com"]:
        t = client._transport_for_url(httpx.URL(target))
        proxied = t is not client._transport
        print(f"   {target}: default_transport={not proxied} "
              f"proxy_url={getattr(t, '_proxy', None) or (getattr(t, 'proxy', None))}")
print("\nexpectation (curl semantics): 192.168.0.5 / 10.0.0.7 / 192.168.0.0 all NOT proxied")
