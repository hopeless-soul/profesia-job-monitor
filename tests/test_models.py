# Lesson 2: a Job survives a trip through a plain dict (store/jobs.py relies on this to read offers back)

from offer_monitor.models import Job, Salary, Verdict


# a job without salary or verdict comes back equal (the None branches in from_dict)
def test_round_trip_without_salary_or_verdict():
    job = Job(id=1, title="Python Developer", url="https://example.com/1", source="profesia")
    assert Job.from_dict(job.to_dict()) == job


# a job with a nested salary and verdict comes back equal, with real Salary / Verdict objects inside
def test_round_trip_with_salary_and_verdict():
    # TODO(human): build a Job with a Salary and a Verdict (and labels), round-trip it, assert it is equal
    ...
