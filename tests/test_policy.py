from datetime import datetime, timedelta, timezone
import pytest
from app.domain.policy import PublicationPolicy, SURFACES
from app.domain.evidence import digest, parse_segments, validate_anchor


@pytest.mark.parametrize('surface',sorted(SURFACES))
@pytest.mark.parametrize('confidence',[0,0.5,1])
def test_unverified_important_fact_never_published(surface,confidence):
    claim={'claim_kind':'factual_assertion','importance':'high','lifecycle_status':'active','extraction_confidence':confidence,'verification_validity':'pending'}
    assert PublicationPolicy.evaluate(surface,claim,None,True)['action']=='hold'


def test_unicode_anchor_and_hallucination():
    segments=parse_segments('😊仅测试版支持，正式版不支持。')
    anchor={'segment_id':'seg_1','char_start':1,'char_end':7,'quote':'仅测试版支持','quote_hash':digest('仅测试版支持')}
    validate_anchor(segments,anchor)
    for bad in [{**anchor,'segment_id':'imagined'}, {**anchor,'char_start':0},{**anchor,'quote':'正式版支持'},{**anchor,'quote_hash':'invented'}]:
        with pytest.raises(ValueError):validate_anchor(segments,bad)


def test_subtitles_keep_timestamps():
    seg=parse_segments('1\n00:00:01,200 --> 00:00:03,400\n你好😊\n\n','srt')[0]
    assert seg['start_ms']==1200 and seg['end_ms']==3400 and seg['text']=='你好😊'


@pytest.mark.parametrize('strength,human,expected',[('E2',False,'hold'),('E2',True,'hold'),('E3',False,'hold'),('E3',True,'allow')])
def test_high_fact_requires_human_and_e3(strength,human,expected):
    claim={'claim_kind':'factual_assertion','importance':'high','lifecycle_status':'active','verification_validity':'current'}
    record={'review_status':'accepted','valid_until':datetime.now(timezone.utc)+timedelta(days=1),'checker_type':'human' if human else 'rule','reviewer_id':'human' if human else None,'assessment':{'status':'corroborated','support_strength':strength,'unresolved_counterevidence':False}}
    assert PublicationPolicy.evaluate('feed',claim,record,True)['action']==expected


def test_stale_and_missing_evidence_fail_closed():
    claim={'claim_kind':'factual_assertion','importance':'high','lifecycle_status':'active','verification_validity':'stale'}
    record={'review_status':'accepted','valid_until':datetime.now(timezone.utc)+timedelta(days=1),'checker_type':'human','reviewer_id':'human','assessment':{'status':'corroborated','support_strength':'E3','unresolved_counterevidence':False}}
    assert PublicationPolicy.evaluate('export',claim,record,True)['action']=='hold'
    assert PublicationPolicy.evaluate('export',claim,record,False)['action']=='retract'
