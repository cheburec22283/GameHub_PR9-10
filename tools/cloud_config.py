import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

FIELDS = {
    "folder_id": "YC_FOLDER_ID",
    "registry_id": "YC_REGISTRY_ID",
    "deploy_service_account_id": "YC_DEPLOY_SA_ID",
    "runtime_service_account_id": "YC_SA_ID",
    "container_name": "YC_CONTAINER_NAME",
    "network_id": "YC_NETWORK_ID",
    "lockbox_secret_id": "YC_LOCKBOX_SECRET_ID",
    "lockbox_version_id": "YC_LOCKBOX_VERSION_ID",
    "otlp_traces_endpoint": "YC_OTLP_TRACES_ENDPOINT",
}


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate configuration field: " + name)
        result[name] = value
    return result


def load_config(path):
    config = json.loads(path.read_text(), object_pairs_hook=unique_object)
    if not isinstance(config, dict):
        raise ValueError("Cloud configuration must be a JSON object")
    if set(config) != {"enabled", *FIELDS}:
        raise ValueError("Cloud configuration has missing or unknown fields")
    if type(config["enabled"]) is not bool:
        raise ValueError("enabled must be a JSON boolean")
    for name in FIELDS:
        value = config[name]
        if not isinstance(value, str):
            raise ValueError(name + " must be a string")
        if any(character in value for character in "\r\n\x00"):
            raise ValueError(name + " contains an invalid character")
        if name == "otlp_traces_endpoint":
            if value:
                url = urlsplit(value)
                valid = url.scheme in {"http", "https"} and url.hostname
                valid = valid and not any((
                    url.username, url.password, url.query, url.fragment,
                ))
                if not valid:
                    raise ValueError("Invalid OTLP endpoint")
        elif name == "container_name":
            pattern = r"[a-z][a-z0-9-]{1,61}[a-z0-9]"
            if value and not re.fullmatch(pattern, value):
                raise ValueError("Invalid container_name")
        elif value and not re.fullmatch(r"[a-z0-9]{1,63}", value):
            raise ValueError(name + " must contain only lowercase IDs")
    if config["lockbox_version_id"] == "latest":
        raise ValueError("Use an explicit Lockbox version ID")
    if config["enabled"]:
        missing = [
            name for name in FIELDS
            if name != "otlp_traces_endpoint" and not config[name]
        ]
        if missing:
            raise ValueError(
                "Missing enabled configuration: " + ", ".join(missing)
            )
    return config


def main():
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / ".github" / "gamehub-cloud.json")
    enabled = str(config["enabled"]).lower()
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"enabled={enabled}\n")
    if config["enabled"] and os.getenv("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a") as environment:
            for name, variable in FIELDS.items():
                environment.write(f"{variable}={config[name]}\n")
    print(f"Cloud deployment enabled: {enabled}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"Invalid cloud configuration: {error}")
