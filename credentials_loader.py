from typing import Dict, Tuple


CredentialMap = Dict[str, Tuple[str, str]]


def load_credentials(path: str = "credentials.txt") -> CredentialMap:
    with open(path) as file:
        lines = [line.rstrip() for line in file if line.strip()]

    if len(lines) % 3 != 0:
        raise ValueError(
            "credentials.txt must contain repeating three-line blocks: "
            "label, handle, password."
        )

    credentials: CredentialMap = {}
    for i in range(0, len(lines), 3):
        label = lines[i]
        handle = lines[i + 1]
        password = lines[i + 2]

        if label in credentials:
            raise ValueError(f"Duplicate account label in credentials.txt: {label}")

        credentials[label] = (handle, password)

    return credentials


def select_credentials(
    credentials: CredentialMap, account: str = "default"
) -> Tuple[str, str]:
    try:
        return credentials[account]
    except KeyError as exc:
        available = ", ".join(sorted(credentials))
        raise KeyError(
            f"Unknown account label '{account}'. Available accounts: {available}"
        ) from exc
