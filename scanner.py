from __future__ import annotations

import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag

GENERIC_LINK_TEXT = {
    "click here",
    "here",
    "read more",
    "learn more",
    "more",
    "this link",
    "link",
    "continue",
    "continue reading",
}

GENERIC_TITLES = {"home", "untitled", "new page", "page", "index", "welcome"}

USER_AGENT = (
    "Mozilla/5.0 (compatible; ContentIssueScanner/1.0; "
    "+https://localhost/content-dashboard)"
)


@dataclass
class Issue:
    severity: str
    category: str
    page_url: str
    title: str
    detail: str
    recommendation: str
    evidence: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class ContentScanner:
    def __init__(
        self,
        start_url: str,
        max_pages: int = 8,
        check_links: bool = True,
        timeout: float = 8.0,
    ) -> None:
        self.start_url = self._normalize_url(start_url)
        parsed = urlparse(self.start_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("Enter a full URL including http:// or https://")

        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.max_pages = max(1, min(int(max_pages), 25))
        self.check_links = check_links
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html"})
        self.session.max_redirects = 5
        self._link_owners: dict[str, str] = {}

    def scan(self) -> dict:
        started = datetime.now(timezone.utc)
        pages: list[dict] = []
        issues: list[Issue] = []
        fetched_urls: list[str] = []
        errors: list[str] = []
        queue = [self.start_url]
        seen: set[str] = set()

        while queue and len(fetched_urls) < self.max_pages:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)

            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            except requests.RequestException as exc:
                errors.append(f"Could not fetch {url}: {exc}")
                issues.append(
                    self._issue(
                        "critical",
                        "Links",
                        url,
                        "Page could not be fetched",
                        str(exc),
                        "Confirm the URL is reachable and not blocked.",
                    )
                )
                continue

            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower() and fetched_urls:
                continue

            soup = BeautifulSoup(response.text, "lxml")
            final_url = self._normalize_url(str(response.url))
            fetched_urls.append(final_url)

            page_issues, page_stats, outbound = self._analyze_page(final_url, response, soup)
            issues.extend(page_issues)
            pages.append({"url": final_url, "status": response.status_code, **page_stats})

            for link in outbound:
                self._link_owners.setdefault(link, final_url)
                if link not in seen and self._same_origin(link) and len(seen) + len(queue) < self.max_pages * 3:
                    queue.append(link)

        if self.check_links:
            issues.extend(self._probe_links(exclude=set(fetched_urls)))

        finished = datetime.now(timezone.utc)
        return {
            "start_url": self.start_url,
            "scanned_at": finished.isoformat(),
            "duration_ms": int((finished - started).total_seconds() * 1000),
            "pages_scanned": len(pages),
            "max_pages": self.max_pages,
            "errors": errors,
            "summary": self._summarize(pages, issues),
            "pages": pages,
            "issues": [issue.to_dict() for issue in issues],
            "categories": self._category_counts(issues),
            "severity": self._severity_counts(issues),
        }

    def _analyze_page(self, url: str, response: requests.Response, soup: BeautifulSoup):
        issues: list[Issue] = []
        text = soup.get_text(" ", strip=True)
        words = re.findall(r"[A-Za-z0-9']+", text)
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        description = self._meta(soup, "description")
        h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
        headings = soup.find_all(re.compile(r"^h[1-6]$"))
        images = soup.find_all("img")
        links = soup.find_all("a")
        outbound: list[str] = []

        if response.status_code >= 400:
            issues.append(
                self._issue(
                    "critical",
                    "Links",
                    url,
                    f"Page returned HTTP {response.status_code}",
                    "Search engines and users cannot reliably access this page.",
                    "Fix the server response or redirect to a valid URL.",
                    str(response.status_code),
                )
            )

        html = soup.find("html")
        lang = (html.get("lang") if html else None) or ""
        if not str(lang).strip():
            issues.append(
                self._issue(
                    "warning",
                    "Accessibility",
                    url,
                    "Missing language attribute",
                    "The <html> tag has no lang attribute.",
                    'Add lang, for example <html lang="en">.',
                )
            )

        if not soup.find("meta", attrs={"charset": True}) and not soup.find(
            "meta", attrs={"http-equiv": re.compile("content-type", re.I)}
        ):
            issues.append(
                self._issue(
                    "info",
                    "Technical",
                    url,
                    "Missing character encoding",
                    "No charset meta tag was found.",
                    'Add <meta charset="utf-8"> in the document head.',
                )
            )

        if not soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}):
            issues.append(
                self._issue(
                    "warning",
                    "Technical",
                    url,
                    "Missing viewport meta tag",
                    "Mobile browsers may not scale the page correctly.",
                    'Add <meta name="viewport" content="width=device-width, initial-scale=1">.',
                )
            )

        if not title:
            issues.append(
                self._issue(
                    "critical",
                    "SEO",
                    url,
                    "Missing page title",
                    "The page has no <title> tag or the title is empty.",
                    "Add a unique title of about 50–60 characters.",
                )
            )
        else:
            if len(title) < 15:
                issues.append(
                    self._issue(
                        "warning",
                        "SEO",
                        url,
                        "Title is too short",
                        f"Title is {len(title)} characters.",
                        "Write a more descriptive title (about 50–60 characters).",
                        title,
                    )
                )
            elif len(title) > 65:
                issues.append(
                    self._issue(
                        "warning",
                        "SEO",
                        url,
                        "Title may be truncated in search results",
                        f"Title is {len(title)} characters.",
                        "Keep titles around 50–60 characters.",
                        title,
                    )
                )
            if title.strip().lower() in GENERIC_TITLES:
                issues.append(
                    self._issue(
                        "warning",
                        "SEO",
                        url,
                        "Generic page title",
                        "The title is too generic to be useful in search results.",
                        "Use a unique, topic-specific title.",
                        title,
                    )
                )

        if not description:
            issues.append(
                self._issue(
                    "warning",
                    "SEO",
                    url,
                    "Missing meta description",
                    "No meta description was found.",
                    "Add a 120–160 character summary of the page.",
                )
            )
        elif len(description) < 50:
            issues.append(
                self._issue(
                    "info",
                    "SEO",
                    url,
                    "Meta description is short",
                    f"Description is {len(description)} characters.",
                    "Expand the description to about 120–160 characters.",
                    description,
                )
            )
        elif len(description) > 170:
            issues.append(
                self._issue(
                    "info",
                    "SEO",
                    url,
                    "Meta description may be truncated",
                    f"Description is {len(description)} characters.",
                    "Keep descriptions around 150 characters.",
                    description[:180],
                )
            )

        if not soup.find("link", rel=lambda value: value and "canonical" in value):
            issues.append(
                self._issue(
                    "info",
                    "SEO",
                    url,
                    "Missing canonical URL",
                    "No rel=canonical tag was found.",
                    "Add a canonical link to prevent duplicate-content confusion.",
                )
            )

        og_title = soup.find("meta", property="og:title")
        og_desc = soup.find("meta", property="og:description")
        if not og_title or not og_desc:
            issues.append(
                self._issue(
                    "info",
                    "SEO",
                    url,
                    "Incomplete Open Graph tags",
                    "og:title or og:description is missing.",
                    "Add Open Graph tags so shares look correct on social platforms.",
                )
            )

        if not h1s:
            issues.append(
                self._issue(
                    "critical",
                    "Content",
                    url,
                    "Missing H1 heading",
                    "The page has no top-level heading.",
                    "Add one clear H1 that describes the page topic.",
                )
            )
        elif len(h1s) > 1:
            issues.append(
                self._issue(
                    "warning",
                    "Content",
                    url,
                    "Multiple H1 headings",
                    f"Found {len(h1s)} H1 tags.",
                    "Keep a single H1 and use H2–H6 for subsections.",
                    " | ".join(h1s[:4]),
                )
            )

        last_level = 0
        heading_skips = 0
        for heading in headings:
            level = int(heading.name[1])
            text_value = heading.get_text(" ", strip=True)
            if not text_value:
                issues.append(
                    self._issue(
                        "warning",
                        "Content",
                        url,
                        f"Empty {heading.name.upper()} heading",
                        "A heading tag has no visible text.",
                        "Remove the empty heading or add meaningful text.",
                    )
                )
            if last_level and level > last_level + 1 and heading_skips < 6:
                heading_skips += 1
                issues.append(
                    self._issue(
                        "info",
                        "Accessibility",
                        url,
                        "Heading level skipped",
                        f"Found {heading.name.upper()} after H{last_level}.",
                        "Keep heading levels sequential (H1 → H2 → H3).",
                        text_value[:80],
                    )
                )
            last_level = level

        missing_alt = 0
        for image in images:
            alt = image.get("alt")
            src = image.get("src") or image.get("data-src") or ""
            if alt is None:
                missing_alt += 1
                if missing_alt <= 20:
                    issues.append(
                        self._issue(
                            "warning",
                            "Accessibility",
                            url,
                            "Image missing alt text",
                            "An <img> tag has no alt attribute.",
                            'Describe the image, or use alt="" if it is decorative.',
                            src[:120],
                        )
                    )

        ids = [tag.get("id") for tag in soup.find_all(attrs={"id": True})]
        duplicates = [key for key, count in Counter(ids).items() if key and count > 1]
        for duplicate_id in duplicates[:8]:
            issues.append(
                self._issue(
                    "warning",
                    "Technical",
                    url,
                    "Duplicate ID attribute",
                    f'id="{duplicate_id}" appears more than once.',
                    "IDs must be unique so assistive tech and in-page links work.",
                    duplicate_id,
                )
            )

        for iframe in soup.find_all("iframe")[:8]:
            title_attr = (iframe.get("title") or "").strip()
            aria = (iframe.get("aria-label") or "").strip()
            if not title_attr and not aria:
                issues.append(
                    self._issue(
                        "warning",
                        "Accessibility",
                        url,
                        "Iframe missing accessible name",
                        "An iframe has no title or aria-label.",
                        "Add a title that explains the embedded content.",
                        (iframe.get("src") or "")[:120],
                    )
                )

        unlabeled = 0
        for input_el in soup.find_all(["input", "select", "textarea"]):
            input_type = (input_el.get("type") or "text").lower()
            if input_type in {"hidden", "submit", "button", "reset", "image"}:
                continue
            if self._has_accessible_name(input_el, soup):
                continue
            unlabeled += 1
            if unlabeled <= 12:
                issues.append(
                    self._issue(
                        "warning",
                        "Accessibility",
                        url,
                        "Form control missing label",
                        "An input does not have an associated label.",
                        "Connect a <label> via for/id or wrap the control in a label.",
                        input_el.get("name") or input_el.get("id") or input_type,
                    )
                )

        for table in soup.find_all("table")[:6]:
            if not table.find("th"):
                issues.append(
                    self._issue(
                        "info",
                        "Accessibility",
                        url,
                        "Table missing header cells",
                        "A table has no <th> cells.",
                        "Use header cells so screen readers can announce columns.",
                    )
                )

        page_is_https = urlparse(url).scheme == "https"
        seen_hrefs: set[str] = set()
        empty_links = 0
        generic_links = 0
        mixed = 0
        noopener = 0

        for anchor in links:
            href = (anchor.get("href") or "").strip()
            label = self._visible_text(anchor)
            has_image = bool(anchor.find("img"))
            if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "sms:")):
                if not href and not label:
                    empty_links += 1
                continue

            if href.startswith("#"):
                target_id = href[1:]
                if target_id and not soup.find(id=target_id):
                    issues.append(
                        self._issue(
                            "warning",
                            "Links",
                            url,
                            "Broken in-page anchor",
                            f'No element has id="{target_id}".',
                            "Fix the fragment or add a matching id on the target element.",
                            href,
                        )
                    )
                continue

            absolute = self._normalize_url(urljoin(url, href))
            parsed_abs = urlparse(absolute)
            if parsed_abs.scheme in {"http", "https"} and absolute not in seen_hrefs:
                seen_hrefs.add(absolute)
                outbound.append(absolute)

            if page_is_https and parsed_abs.scheme == "http" and mixed < 8:
                mixed += 1
                issues.append(
                    self._issue(
                        "warning",
                        "Technical",
                        url,
                        "Mixed content link",
                        "An HTTPS page links to an HTTP resource.",
                        "Use HTTPS URLs to avoid browser mixed-content warnings.",
                        absolute[:160],
                    )
                )

            rel = (anchor.get("rel") or "")
            rel_text = " ".join(rel).lower() if isinstance(rel, list) else str(rel).lower()
            if anchor.get("target") == "_blank" and "noopener" not in rel_text and noopener < 8:
                noopener += 1
                issues.append(
                    self._issue(
                        "info",
                        "Technical",
                        url,
                        "New-tab link missing rel=noopener",
                        'target="_blank" without rel="noopener" can leak the opener window.',
                        'Add rel="noopener noreferrer" to external new-tab links.',
                        absolute[:160],
                    )
                )

            if not label and not has_image:
                empty_links += 1
                if empty_links <= 12:
                    issues.append(
                        self._issue(
                            "warning",
                            "Accessibility",
                            url,
                            "Link has no accessible text",
                            "An anchor has no visible text or image alt.",
                            "Add descriptive link text.",
                            absolute[:160],
                        )
                    )
            elif label.lower() in GENERIC_LINK_TEXT:
                generic_links += 1
                if generic_links <= 12:
                    issues.append(
                        self._issue(
                            "info",
                            "Content",
                            url,
                            "Generic link text",
                            f"Link text “{label}” does not describe the destination.",
                            "Use descriptive link text, such as the article or page name.",
                            absolute[:160],
                        )
                    )

        if len(words) < 120 and soup.find("body"):
            issues.append(
                self._issue(
                    "warning",
                    "Content",
                    url,
                    "Thin page content",
                    f"Only about {len(words)} words of visible text were found.",
                    "Add unique, useful copy so the page can rank and inform users.",
                )
            )

        if not soup.find("link", rel=lambda value: value and "icon" in str(value).lower()):
            issues.append(
                self._issue(
                    "info",
                    "Technical",
                    url,
                    "No favicon declared",
                    "No icon link tag was found in the document head.",
                    'Add a favicon via <link rel="icon" href="...">.',
                )
            )

        stats = {
            "title": title,
            "word_count": len(words),
            "h1_count": len(h1s),
            "heading_count": len(headings),
            "image_count": len(images),
            "missing_alt": missing_alt,
            "link_count": len(links),
            "empty_links": empty_links,
            "generic_links": generic_links,
        }
        return issues, stats, outbound

    def _probe_links(self, exclude: set[str]) -> list[Issue]:
        issues: list[Issue] = []
        candidates = [url for url in self._link_owners if url not in exclude][:40]
        if not candidates:
            return issues

        def probe(url: str) -> tuple[str, int | None, str]:
            try:
                response = self.session.head(url, timeout=self.timeout, allow_redirects=True)
                if response.status_code in {405, 403, 400} or response.status_code >= 500:
                    response = self.session.get(url, timeout=self.timeout, allow_redirects=True, stream=True)
                    response.close()
                return url, response.status_code, ""
            except requests.RequestException as exc:
                return url, None, str(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(probe, url) for url in candidates]
            for future in as_completed(futures):
                url, status, error = future.result()
                owner = self._link_owners.get(url, url)
                if error:
                    issues.append(
                        self._issue(
                            "critical",
                            "Links",
                            owner,
                            "Broken or unreachable link",
                            error,
                            "Update or remove the link.",
                            url,
                        )
                    )
                elif status and status >= 400:
                    issues.append(
                        self._issue(
                            "critical" if status >= 500 or status == 404 else "warning",
                            "Links",
                            owner,
                            f"Link returned HTTP {status}",
                            "The destination responded with an error status.",
                            "Fix the URL, add a redirect, or remove the link.",
                            url,
                        )
                    )
        return issues

    def _has_accessible_name(self, element: Tag, soup: BeautifulSoup) -> bool:
        if (element.get("aria-label") or "").strip():
            return True
        if element.get("aria-labelledby"):
            return True
        if (element.get("title") or "").strip():
            return True
        element_id = element.get("id")
        if element_id and soup.find("label", attrs={"for": element_id}):
            return True
        if element.find_parent("label"):
            return True
        return False

    @staticmethod
    def _visible_text(element: Tag) -> str:
        parts = [element.get_text(" ", strip=True)]
        for image in element.find_all("img"):
            alt = (image.get("alt") or "").strip()
            if alt:
                parts.append(alt)
        return " ".join(part for part in parts if part).strip()

    @staticmethod
    def _meta(soup: BeautifulSoup, name: str) -> str:
        tag = soup.find("meta", attrs={"name": re.compile(f"^{name}$", re.I)})
        return (tag.get("content") or "").strip() if tag else ""

    @staticmethod
    def _issue(
        severity: str,
        category: str,
        page_url: str,
        title: str,
        detail: str,
        recommendation: str,
        evidence: str = "",
    ) -> Issue:
        return Issue(severity, category, page_url, title, detail, recommendation, evidence)

    def _same_origin(self, url: str) -> bool:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}" == self.origin

    @staticmethod
    def _normalize_url(url: str) -> str:
        url = url.strip()
        if url and not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        parsed = urlparse(url)
        path = parsed.path or "/"
        cleaned = parsed._replace(fragment="")
        if path != "/" and path.endswith("/"):
            cleaned = cleaned._replace(path=path.rstrip("/"))
        return urlunparse(cleaned)

    @staticmethod
    def _summarize(pages: list[dict], issues: list[Issue]) -> dict:
        severity = Counter(issue.severity for issue in issues)
        score = 100
        score -= severity.get("critical", 0) * 12
        score -= severity.get("warning", 0) * 4
        score -= severity.get("info", 0) * 1
        score = max(0, min(100, score))
        if score >= 85:
            grade = "Good"
        elif score >= 65:
            grade = "Needs work"
        else:
            grade = "Poor"
        return {
            "score": score,
            "grade": grade,
            "critical": severity.get("critical", 0),
            "warning": severity.get("warning", 0),
            "info": severity.get("info", 0),
            "total_issues": len(issues),
            "avg_words": int(sum(page.get("word_count", 0) for page in pages) / len(pages)) if pages else 0,
        }

    @staticmethod
    def _category_counts(issues: Iterable[Issue]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for issue in issues:
            counts[issue.category] += 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    @staticmethod
    def _severity_counts(issues: Iterable[Issue]) -> dict[str, int]:
        counts = {"critical": 0, "warning": 0, "info": 0}
        for issue in issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        return counts
