from atproto import Client, client_utils
import sqlite3
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class Image:
    path: str
    alt_text: Optional[str] = None

@dataclass
class Post:
    text: str
    images: List[Image] = None

def schedulePost(platform, text, image, image_alt, run_at):
    cur.execute(
        "INSERT INTO scheduled_posts (platform, text, image, image_alt, run_at, status) VALUES (?, ?, ?, ?, ?, ?)",
        (platform, text, image, image_alt, run_at, "pending")
    )

def workerLoop(creds):
    while True:
        now = datetime.now(timezone.utc)

        cur.execute("""
            SELECT id, platform, text, image, image_alt FROM scheduled_posts
            WHERE status = 'pending' AND run_at <= ?
        """, (now,))

        jobs = cur.fetchall()

        for job_id, platform, text, image, image_alt in jobs:
            try:
                if platform == "bluesky":
                    sendpic(creds, text, image, image_alt)
                    print("Do we get to here?")

            except Exception as e:
                cur.execute(
                    "UPDATE scheduled_posts SET status = 'failed' WHERE id = ?",
                    (job_id,)
                )
                print("Or here?")

        conn.commit()
        time.sleep(10)

def getCredentials():
    with open('credentials.txt') as file:
        creds = [line.rstrip() for line in file]
    return creds

def sendpic(creds, text, image, image_alt) -> None:
    handle = creds[0]
    password = creds[1]

    client = Client()
    client.login(handle, password)

    # replace the path to your image file
    with open(image, 'rb') as f:
        img_data = f.read()

    # Add image aspect ratio to prevent default 1:1 aspect ratio
    # Replace with your desired aspect ratio
    #aspect_ratio = models.AppBskyEmbedDefs.AspectRatio(height=100, width=100)

    client.send_image(
        text=text,
        image=img_data,
        image_alt=image_alt#,
        #image_aspect_ratio=aspect_ratio,
    )

def richText(creds) -> None:
    client = Client()
    client.login(creds[0],creds[1])

    client.send_post(client_utils.TextBuilder().text('Hey everyone I made a ').link('Pixiv', 'https://www.pixiv.net/en/users/123184015').text('.'))


if __name__ == '__main__':
    conn = sqlite3.connect('scheduler.db')
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY,
                platform TEXT,
                text TEXT,
                image TEXT,
                image_alt TEXT,
                run_at DATETIME,
                status TEXT)
    """)


    run_at = datetime.strptime("26/04/26 6:00", "%d/%m/%y %H:%M")
    run_at = datetime.now(timezone.utc) #temp for testing
    creds = getCredentials()
    schedulePost("bluesky", "", "C:\\Users\\sarah\\Pictures\\surprise_knuckles.png", "Knuckles from Sonic with the Pikachu Surprise Face", run_at)
    workerLoop(creds)