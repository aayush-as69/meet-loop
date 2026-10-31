"""Turn approved action items into GitHub issues. Nothing here runs except from the confirm endpoint."""
import config
import db
import github


class NotConfigured(Exception):
    """GITHUB_TOKEN / GITHUB_REPO missing or malformed. Raised before anything is posted."""


def credentials() -> tuple[str, str]:
    token, repo = config.github_token(), config.github_repo()
    if not token:
        raise NotConfigured("GITHUB_TOKEN is not set. Add it to the .env file (or the environment) and "
                            "restart; nothing was posted.")
    if not repo:
        raise NotConfigured("GITHUB_REPO is not set. Add it to the .env file as owner/name, e.g. "
                            "GITHUB_REPO=acme/website; nothing was posted.")
    if not github.valid_repo(repo):
        raise NotConfigured(f"GITHUB_REPO must look like owner/name, not '{repo}'; nothing was posted.")
    return token, repo


def issue_title(item: dict) -> str:
    t = " ".join(item["description"].split())
    return t if len(t) <= 250 else t[:249] + "…"


def issue_body(item: dict, meeting: dict, meeting_url: str) -> str:
    quote = "\n".join("> " + line if line.strip() else ">" for line in item["source_text"].strip().splitlines())
    when = meeting["meeting_date"] or "date not recorded"
    return (f"**Owner:** {item['owner'] or 'Unassigned'}\n"
            f"**Due:** {item['deadline'] or 'No due date'}\n\n"
            f"**Source quote**\n{quote}\n\n"
            f"From [{meeting['title']}]({meeting_url}) ({when}).")


def post_meeting(meeting: dict, meeting_url: str) -> dict:
    """Create one issue per approved action item that has none yet. A failure on one item is
    recorded and the rest carry on. Returns {"posted": [...], "skipped": [...], "errors": [...]}."""
    token, repo = credentials()
    out = {"posted": [], "skipped": [], "errors": []}
    for item in db.approved_action_items(meeting["id"]):
        iid = item["id"]
        if item["posted"]:
            out["skipped"].append({"item_id": iid, "reason": "already posted",
                                   "issue_number": item["issue_number"], "issue_url": item["issue_url"]})
            continue
        if not db.claim_for_posting(iid):
            out["skipped"].append({"item_id": iid, "reason": "already posted or being posted"})
            continue
        try:
            issue = github.create_issue(token, repo, issue_title(item), issue_body(item, meeting, meeting_url))
        except github.GitHubError as e:
            db.release_claim(iid)
            out["errors"].append({"item_id": iid, "error": str(e)})
            continue
        except Exception:
            db.release_claim(iid)
            out["errors"].append({"item_id": iid, "error": "Unexpected error while creating the issue."})
            continue
        try:
            db.save_issue(iid, issue["number"], issue["url"])
        except Exception:
            # The issue exists on GitHub but we couldn't record it. Keep the claim so a retry doesn't
            # create a duplicate, and give the person what they need to fix it by hand.
            out["errors"].append({"item_id": iid, "error": f"Issue #{issue['number']} was created ({issue['url']}) "
                                  "but saving it failed. Don't post this item again."})
            continue
        out["posted"].append({"item_id": iid, "issue_number": issue["number"], "issue_url": issue["url"]})
    return out
