from job_agent.matching import prefilter
from job_agent.models import JobPosting, Preferences


def _job(**kw):
    base = dict(source="linkedin", title="Python Developer", company="Acme Pvt Ltd", location="Pune, India",
                url="https://example.com/1")
    base.update(kw)
    return JobPosting(**base)


def test_upsert_dedups_across_sources(store):
    a, new_a = store.upsert_job(_job())
    b, new_b = store.upsert_job(_job(source="naukri", url="https://n/1", company="Acme Private Limited",
                                     description="Full description"))
    assert new_a and not new_b and a == b
    assert store.get_job(a)["description"] == "Full description"
    c, new_c = store.upsert_job(_job())
    assert c == a and not new_c


def test_answers_and_companies(store):
    store.save_answer("Notice period?", "30 days")
    store.save_answer("notice period", "15 days")
    assert store.list_answers() == [{"question": "notice period", "answer": "15 days"}]
    assert store.delete_answer("Notice Period")
    assert any(c["slug"] == "databricks" for c in store.list_companies())


def test_prefilter_ranks_and_excludes(store, profile):
    prefs = Preferences(roles=["Python Developer"], exclude_companies=["BadCo"], job_types=["fulltime"])
    good = {"title": "Python Developer", "company": "Acme", "description": "Django, SQL, Docker, REST APIs", "skills": []}
    senior = {"title": "Senior Python Developer", "company": "Acme", "description": "8+ years. Django", "skills": [],
              "experience_text": "8-12 Yrs"}
    other = {"title": "Sales Executive", "company": "Acme", "description": "Field sales", "skills": []}
    bad = {"title": "Python Developer", "company": "BadCo Pvt", "description": "", "skills": []}
    s_good, _ = prefilter(good, profile, prefs)
    s_senior, _ = prefilter(senior, profile, prefs)
    s_other, _ = prefilter(other, profile, prefs)
    _, drop = prefilter(bad, profile, prefs)
    assert s_good > 0.7
    assert s_senior < s_good - 0.3  # too senior for a 1-year candidate
    assert s_other < 0.2
    assert drop == "excluded company BadCo"
