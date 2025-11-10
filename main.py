import os, io, re, random, zipfile, requests
from fpdf import FPDF
import praw, pytumblr
from config import *

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
SUBREDDITS = [s.strip() for s in os.getenv("SUBREDDITS", "ArtPrompts").split(",") if s.strip()]
MIRROR_URL_OVERRIDE = os.getenv("MIRROR_URL", "")
GUTENDEX = "https://gutendex.com/books"

def strip_headers(text):
    s = re.search(r"\*\*\* START OF.*? \*\*\*", text, re.I)
    e = re.search(r"\*\*\* END OF.*? \*\*\*", text, re.I)
    return text[s.end():e.start()].strip() if s and e else text.strip()

def fetch_book_meta(book_id):
    r = requests.get(f"{GUTENDEX}/{book_id}", timeout=30)
    r.raise_for_status()
    meta = r.json()
    fmts = meta.get("formats", {})
    url = fmts.get("text/plain; charset=utf-8") or fmts.get("text/plain") or fmts.get("application/zip")
    if not url:
        raise RuntimeError(f"No plain text for {book_id}")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    if url.endswith(".zip"):
        z = zipfile.ZipFile(io.BytesIO(resp.content))
        name = next((n for n in z.namelist() if n.endswith(".txt")), z.namelist()[0])
        raw = z.read(name).decode("utf-8", errors="ignore")
    else:
        resp.encoding = "utf-8"
        raw = resp.text
    text = strip_headers(raw)
    title = meta.get("title", f"Book {book_id}")
    authors = ", ".join([a["name"] for a in meta.get("authors", [])]) or "Unknown"
    return {"id": book_id, "title": title, "authors": authors, "text": text}

IMPERATIVE = {"create","build","practice","write","plan","focus","consider","review","identify","remove","simplify","measure","reflect","define","limit","choose","start","stop","avoid","apply","analyze","organize"}
ACTION = IMPERATIVE | {"should","must","let","try","set","schedule","decide","commit","track"}

def actionable_sentences(text, max_n=25):
    sents = re.split(r'(?<=[.!?])\s+', text)
    cand = []
    for s in sents:
        t = s.strip()
        w = len(t.split())
        if not 6 <= w <= 22:
            continue
        lower = t.lower()
        first = lower.split()[0].strip('""(') if lower else ""
        score = 0
        if first in IMPERATIVE:
            score += 1.0
        if any(wd in lower for wd in ACTION):
            score += 0.5
        if t.count('"') > 2:
            score -= 0.5
        if len(t) > 220:
            score -= 0.5
        if score > 0.5:
            cand.append((score, t))
    cand.sort(reverse=True, key=lambda x: x[0])
    out = [t for _, t in cand[:max_n]]
    if len(out) < max_n//2:
        out += [x.strip() for x in sents if 6 <= len(x.split()) <= 18][:(max_n - len(out))]
    return out[:max_n]

def load_font(pdf):
    try:
        pdf.add_font("DejaVu", "", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", uni=True)
        pdf.set_font("DejaVu", size=12)
    except:
        pdf.set_font("Helvetica", size=12)

def ensure_github_mirror(pdf_path, product_title, slug):
    if MIRROR_URL_OVERRIDE:
        return MIRROR_URL_OVERRIDE
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        return ""
    owner, repo_name = repo.split("/")
    create_url = f"https://api.github.com/repos/{owner}/{repo_name}/releases"
    tag = f"toolkit-{slug}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    data = {"tag_name": tag, "name": product_title, "body": "Auto-generated toolkit", "draft": False, "prerelease": False}
    r = requests.post(create_url, headers=headers, json=data, timeout=30)
    if r.status_code == 422:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo_name}/releases/tags/{tag}", headers=headers, timeout=30)
    r.raise_for_status()
    release = r.json()
    upload_url = release.get("upload_url", "").split("{")[0]
    if not upload_url:
        return ""
    filename = os.path.basename(pdf_path)
    with open(pdf_path, "rb") as f:
        urla = f"{upload_url}?name={filename}"
        hr = {"Authorization": f"Bearer {token}", "Content-Type": "application/pdf", "Accept": "application/vnd.github+json"}
        ra = requests.post(urla, headers=hr, data=f.read(), timeout=120)
    if ra.status_code == 422:
        la = requests.get(release["assets_url"], headers=headers, timeout=30)
        if la.status_code == 200:
            for asset in la.json():
                if asset.get("name") == filename:
                    return asset.get("browser_download_url", "")
    ra.raise_for_status()
    asset = ra.json()
    return asset.get("browser_download_url", "")

class AutoContentSystem:
    def __init__(self):
        self.payhip_api_key = PAYHIP_API_KEY
        self.reddit = praw.Reddit(client_id=REDDIT_CLIENT_ID, client_secret=REDDIT_CLIENT_SECRET, user_agent=REDDIT_USER_AGENT, username=REDDIT_USERNAME, password=REDDIT_PASSWORD)
        self.tumblr = pytumblr.TumblrRestClient(TUMBLR_CONSUMER_KEY, TUMBLR_CONSUMER_SECRET, TUMBLR_OAUTH_TOKEN, TUMBLR_OAUTH_SECRET)

    def generate_pdf(self, meta, theme):
        pdf = FPDF(format="A4")
        pdf.set_margins(15, 15, 15)
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        load_font(pdf)
        pdf.set_font_size(20)
        pdf.multi_cell(0, 10, f"{theme} Toolkit: {meta['title']}")
        pdf.set_font_size(12)
        pdf.multi_cell(0, 7, f"Source: {meta['authors']} (Public Domain)")
        pdf.ln(3)
        pdf.multi_cell(0, 7, "How to use: read one quote/day, answer the 5-minute prompt, take one action.")
        pdf.ln(3)
        for i, s in enumerate(actionable_sentences(meta["text"], 25), 1):
            pdf.set_font_size(13)
            pdf.multi_cell(0, 7, f"{i}. Quote")
            pdf.set_font_size(12)
            pdf.set_text_color(90)
            pdf.multi_cell(0, 6, f"> {s}")
            pdf.set_text_color(0)
            pdf.ln(1)
            pdf.multi_cell(0, 6, f"Prompt: How can you apply this to your {theme.lower()} this week? Name one friction and one action.")
            pdf.ln(2)
            pdf.cell(0, 0, "", border="B")
            pdf.ln(3)
        os.makedirs("products", exist_ok=True)
        slug = f"{theme.lower().replace(' ','_')}_{meta['id']}"
        path = f"products/{slug}.pdf"
        pdf.output(path)
        title = f"{theme} Toolkit - {meta['title']}"
        return path, title, slug

    def create_payhip_product(self, pdf_path, title):
        url = "https://payhip.com/api/v2/product"
        headers = {"payhip-api-key": self.payhip_api_key}
        with open(pdf_path, 'rb') as f:
            files = {'file': (os.path.basename(pdf_path), f, 'application/pdf')}
            data = {'product_title': title, 'product_price': PRODUCT_PRICE, 'product_currency': PRODUCT_CURRENCY, 'product_category': PRODUCT_CATEGORY, 'product_sku': f"pd-{os.path.splitext(os.path.basename(pdf_path))[0]}"}
            r = requests.post(url, headers=headers, files=files, data=data, timeout=120)
        if r.status_code in (200, 201):
            try:
                j = r.json()
                return j.get('data', {}).get('product', {}).get('url') or j.get('url', '')
            except:
                return ''
        raise Exception(f"Payhip API error {r.status_code}: {r.text}")

    def post_to_reddit(self, title, body):
        for sub in SUBREDDITS:
            try:
                self.reddit.subreddit(sub).submit(title=title, selftext=body)
            except Exception as e:
                print(f"[reddit:{sub}] {e}")

    def post_to_tumblr(self, title, body):
        try:
            blog = os.getenv("TUMBLR_BLOG_NAME", "").replace("https://","").replace("http://","").replace(".tumblr.com","")
            self.tumblr.create_text(blog, state="published", title=title, body=body, tags=["free","toolkit","publicdomain","writingtools"])
        except Exception as e:
            print(f"[tumblr] {e}")

    def run(self):
        try:
            book_id = random.choice(BOOK_IDS)
            meta = fetch_book_meta(book_id)
            theme = "Stoic Productivity" if "Meditations" in meta["title"] else "Creative Focus"
            pdf_path, product_title, slug = self.generate_pdf(meta, theme)
            mirror_url = ensure_github_mirror(pdf_path, product_title, slug)
            product_url = ""
            if not DRY_RUN:
                try:
                    product_url = self.create_payhip_product(pdf_path, product_title) or ""
                except Exception as e:
                    print(f"[payhip] {e}")
            parts = ["Free community edition from public domain sources.", "", "What's inside:", "- 25 quotes", "- 25 five-minute prompts", "- Printable page"]
            if mirror_url:
                parts += ["", f"Direct download: {mirror_url}"]
            if product_url:
                parts += ["", f"Optional PWYW store: {product_url}?utm_source=reddit&utm_medium=post&utm_campaign=toolkits"]
            body = "\n".join(parts)
            if not DRY_RUN:
                self.post_to_reddit(f"{product_title} (free)", body)
                self.post_to_tumblr(product_title, body)
            return f"Done: {product_title} | mirror: {mirror_url or 'none'} | store: {product_url or 'none'}"
        except Exception as e:
            return f"Error: {str(e)}"

if __name__ == "__main__":
    print(AutoContentSystem().run())
