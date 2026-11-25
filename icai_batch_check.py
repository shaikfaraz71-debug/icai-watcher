"""
One-shot ICAI batch check, designed to run every 5 minutes on GitHub Actions.
Alerts via ntfy.sh when a batch has seats > 0, or when the batch table changes.
"""
import hashlib
import os
import pathlib
import re

import requests
from playwright.sync_api import sync_playwright

URL = "https://www.icaionlineregistration.org/launchbatchdetail.aspx"
REGION = "Southern"
POU = "Hyderabad"
COURSE = "Advanced (ICITSS) MCS Course"

NTFY_TOPIC = os.environ["NTFY_TOPIC"]  # set as a GitHub secret
STATE = pathlib.Path("state.txt")


def notify(msg: str) -> None:
    print("ALERT:", msg, flush=True)
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=msg.encode("utf-8"),
        headers={"Title": "ICAI batch alert", "Priority": "urgent", "Tags": "rotating_light"},
        timeout=15,
    )


def pick(page, idx: int, text: str) -> None:
    sel = page.locator("select").nth(idx)
    opts = sel.locator("option").all_inner_texts()
    label = next((o for o in opts if o.strip().lower() == text.lower()), None)
    if label is None:
        label = next(o for o in opts if text.lower() in o.lower())
    sel.select_option(label=label)  # ASP.NET postback
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)


def read_rows(page) -> list[list[str]]:
    page.goto(URL, wait_until="networkidle")
    pick(page, 0, REGION)
    pick(page, 1, POU)
    pick(page, 2, COURSE)

    btn = page.get_by_role("button", name=re.compile(r"search|submit|show|view|get|go", re.I))
    if not btn.count():
        btn = page.locator("input[type=submit], input[type=button], button")
    if btn.count():
        btn.first.click()
        page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)

    # ---- DEBUG OUTPUT (shows in the Actions log) ----
    print("URL:", page.url)
    for i in range(page.locator("select").count()):
        chosen = page.locator("select").nth(i).evaluate(
            "e => e.options[e.selectedIndex] ? e.options[e.selectedIndex].text : ''"
        )
        print(f"select {i} = {chosen}")
    print("BUTTONS:", page.locator("input[type=submit], input[type=button], button").evaluate_all(
        "els => els.map(e => e.value || e.innerText || e.id)"))
    print("PAGE TEXT:", page.inner_text("body")[:3000])
    print("---- END DEBUG ----")

    rows = []
    for tr in page.locator("table tr").all():
        cells = [c.strip() for c in tr.locator("th,td").all_inner_texts()]
        if any(cells):
            rows.append(cells)
    return rows


def open_batches(rows: list[list[str]]) -> list[list[str]]:
    seat_col = None
    out = []
    for r in rows:
        if seat_col is None:
            for i, c in enumerate(r):
                if "seat" in c.lower():
                    seat_col = i
                    break
            continue
        if seat_col < len(r):
            m = re.search(r"\d+", r[seat_col])
            if m and int(m.group()) > 0:
                out.append(r)
    return out


def main() -> None:
    # Manual "Run workflow" clicks always send a test ping to the phone.
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        notify(f"Test alert from GitHub (manual run). Topic length: {len(NTFY_TOPIC)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        rows = read_rows(page)
        browser.close()

    for r in rows:  # visible in the Actions log, useful for first-time checking
        print(r)

    h = hashlib.md5(repr(rows).encode()).hexdigest()
    prev = STATE.read_text().strip() if STATE.exists() else None
    avail = open_batches(rows)

    if avail:
        notify("SEATS OPEN! " + " | ".join(" / ".join(r) for r in avail[:3]) + f"\n{URL}")
    elif prev and prev != h:
        notify("Batch table changed (new batch launched?). Check now:\n" + URL)
    else:
        print("No seats, no change.")

    STATE.write_text(h)


if __name__ == "__main__":
    main()
