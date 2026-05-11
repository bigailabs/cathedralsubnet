import os
import requests

from scalecodec.type_registry import SUPPORTED_TYPE_REGISTRY_PRESETS, ONLINE_BASE_URL


def update_type_registries():
    for type_registry in SUPPORTED_TYPE_REGISTRY_PRESETS:
        type_registry_json = f"{type_registry}.json"
        result = requests.get(f"{ONLINE_BASE_URL}{type_registry_json}")

        if result.status_code == 200:
            remote_type_reg = result.content

            module_path = os.path.dirname(__file__)
            path = os.path.join(module_path, "type_registry", type_registry_json)

            f = open(path, "wb")
            f.write(remote_type_reg)
            f.close()


if __name__ == "__main__":
    print("Updating type registries...")
    update_type_registries()
    print("Type registries updated")
