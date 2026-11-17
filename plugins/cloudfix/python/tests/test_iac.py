from cloudfix.common.iac import iac_signals


def test_signals():
    assert iac_signals({"aws:cloudformation:stack-name": "s"})
    assert iac_signals({"pulumi:project": "p"})
    assert iac_signals({"ManagedBy": "Terraform"}) and iac_signals({"terraform": "true"})
    assert iac_signals({"kubernetes.io/created-for/pvc/name": "data"})
    assert iac_signals({"ManagedBy": "ops-team"}) == [] and iac_signals({"Name": "db"}) == []
