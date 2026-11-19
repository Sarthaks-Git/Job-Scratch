from pathlib import Path

from job_agent.sources import company_boards, internshala
from job_agent.sources.base import SearchQuery, html_to_text
from job_agent.sources.jobspy_boards import row_to_posting

FIX = Path(__file__).parent / "fixtures"


def test_internshala_parse_listing():
    cards = internshala.parse_listing((FIX / "internshala_listing.html").read_text())
    assert len(cards) == 2
    a, b = cards
    assert a["title"] == "Python Development" and a["company"] == "Acme Labs"
    assert a["location"] == "Bangalore" and a["pay"].startswith("₹ 10,000")
    assert a["skills"] == ["Python", "Django"] and a["kind"] == "internship"
    assert a["url"].startswith("https://internshala.com/internship/detail/")
    assert b["kind"] == "job" and b["experience"] == "1 year(s)" and "/year" in b["pay"]


def test_internshala_days_ago():
    assert internshala._days_ago("2 days ago") == 2
    assert internshala._days_ago("3 weeks ago") == 21
    assert internshala._days_ago("Today") == 0


def test_jobspy_row_mapping_handles_nan():
    row = {"id": "nk-1", "title": "Data Analyst", "company": "X", "location": "Pune, India",
           "job_url": "https://www.naukri.com/job-listings-1", "job_url_direct": float("nan"),
           "min_amount": 500000.0, "max_amount": 800000.0, "currency": "INR", "interval": "yearly",
           "skills": "SQL, Excel", "experience_range": "1-3 Yrs", "is_remote": False,
           "description": float("nan"), "date_posted": "2026-11-01"}
    p = row_to_posting("naukri", row)
    assert p.apply_url == p.url and p.description is None
    assert p.skills == ["SQL", "Excel"] and p.salary_text == "INR 500,000 - 800,000 yearly"


def test_html_to_text_unescapes_greenhouse_content():
    assert html_to_text("&lt;p&gt;Hello&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;") == "Hello\n- Python"


class _Resp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, data):
        self.data = data

    def get(self, url, **kw):
        return _Resp(self.data)


def test_company_boards_keep_india_and_relevant_titles(monkeypatch):
    gh = {"jobs": [
        {"id": 1, "title": "Backend Engineer (Python)", "absolute_url": "https://x/1", "location": {"name": "Bengaluru, India"}},
        {"id": 2, "title": "Backend Engineer (Python)", "absolute_url": "https://x/2", "location": {"name": "Dublin, Ireland"}},
        {"id": 3, "title": "Account Executive", "absolute_url": "https://x/3", "location": {"name": "Mumbai, India"}},
    ]}
    monkeypatch.setattr(company_boards, "http", lambda: _Session(gh))
    q = SearchQuery(roles=["Python Developer"], locations=[], remote_ok=True, job_types=["fulltime"],
                    posted_within_days=14, results_per_query=10, max_queries=2)
    res = company_boards.search(q, [{"ats": "greenhouse", "slug": "acme", "name": "Acme"}])
    assert [j.url for j in res.jobs] == ["https://x/1"]
    assert res.jobs[0].source == "company:greenhouse"


def test_lever_india_country_code(monkeypatch):
    lever = [{"id": "a", "text": "Data Analyst", "hostedUrl": "https://l/a", "applyUrl": "https://l/a/apply",
              "country": "IN", "categories": {"location": "Hyderabad", "commitment": "Full Time"},
              "descriptionPlain": "SQL and dashboards", "lists": [], "additionalPlain": ""}]
    monkeypatch.setattr(company_boards, "http", lambda: _Session(lever))
    jobs = company_boards.fetch_company("lever", "acme", "Acme")
    assert len(jobs) == 1 and jobs[0].location == "Hyderabad, India" and jobs[0].job_type == "fulltime"


def test_query_combos_capped():
    q = SearchQuery(roles=["A", "B", "C"], locations=["Pune", "Remote"], remote_ok=True, job_types=["fulltime"],
                    posted_within_days=7, results_per_query=5, max_queries=4)
    combos = q.combos()
    assert len(combos) == 4 and ("A", "Pune") in combos and ("B", "Pune") in combos
