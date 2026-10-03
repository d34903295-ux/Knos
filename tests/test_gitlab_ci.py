from __future__ import annotations

import re
from pathlib import Path
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

def test_gitlab_ci_example_in_install_docs():
    """Verify docs/INSTALL.md contains a valid GitLab CI example that executes knos check."""
    doc_path = ROOT / "docs" / "INSTALL.md"
    assert doc_path.exists(), "docs/INSTALL.md must exist"
    
    content = doc_path.read_text(encoding="utf-8")
    
    # Locate the yaml code block containing .gitlab-ci.yml
    matches = re.findall(r"```yaml\n(# \.gitlab-ci\.yml[\s\S]*?)```", content)
    assert len(matches) >= 1, "Expected a fenced yaml block starting with '# .gitlab-ci.yml' in docs/INSTALL.md"
    
    parsed = yaml.safe_load(matches[0])
    assert isinstance(parsed, dict), "Parsed GitLab CI block must be a YAML dictionary"
    
    # Verify that at least one job executes `knos check`
    has_knos_check = False
    for job_name, job_body in parsed.items():
        if isinstance(job_body, dict):
            scripts = job_body.get("script", [])
            if isinstance(scripts, str):
                scripts = [scripts]
            if any("knos check" in s for s in scripts):
                has_knos_check = True
                break
                
    assert has_knos_check, "GitLab CI configuration must define a job that executes 'knos check'"
