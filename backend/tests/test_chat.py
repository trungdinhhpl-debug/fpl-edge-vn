"""Trợ lý hỏi–đáp: nhận diện ý định + bám dữ liệu (không bịa)."""
from sqlalchemy import select

from app.models import Player
from app.services.chat import answer_question, find_players


def test_captain_intent(db):
    res = answer_question(db, "Ai nên làm đội trưởng?")
    assert "Đội trưởng" in res["answer"]
    assert res["grounded"] is True
    assert res["players"]                      # có kèm cầu thủ cụ thể


def test_captain_intent_without_accents(db):
    """Người dùng hay gõ không dấu."""
    res = answer_question(db, "ai nen lam doi truong")
    assert "Đội trưởng" in res["answer"]


def test_player_lookup_returns_numbers(db):
    p = db.scalars(
        select(Player).where(Player.minutes > 0).order_by(Player.total_points.desc())
    ).first()
    res = answer_question(db, f"{p.web_name} có nên mua không?")
    assert p.web_name in res["answer"]
    assert "xP" in res["answer"]                # luôn kèm số liệu
    assert res["players"] == [p.id]


def test_fixtures_and_news_intents(db):
    assert "Lịch thi đấu" in answer_question(db, "Đội nào có lịch dễ?")["answer"]
    news = answer_question(db, "cầu thủ nào đang chấn thương")["answer"]
    assert "chấn thương" in news.lower() or "cảnh báo" in news.lower()


def test_position_and_budget_filter(db):
    res = answer_question(db, "tiền đạo nào tốt nhất dưới 7 triệu?")
    assert "tiền đạo" in res["answer"]
    assert "£7.0m" in res["answer"]


def test_unknown_question_falls_back_to_help(db):
    res = answer_question(db, "zzz qqq xxx")
    assert "bạn có thể hỏi" in res["answer"].lower()
    assert res["suggestions"]


def test_name_matching_prefers_more_specific_player(db):
    """Trùng họ thì phải chọn đúng người, không lấy bừa."""
    players = db.scalars(select(Player)).all()
    surnames: dict[str, list[Player]] = {}
    for p in players:
        surnames.setdefault((p.second_name or "").lower(), []).append(p)
    dupes = [v for k, v in surnames.items() if k and len(v) > 1]
    if not dupes:
        return  # bộ dữ liệu demo có thể không có trùng họ
    group = max(dupes, key=lambda g: max(x.minutes for x in g))
    found = find_players(db, group[0].second_name)
    assert len(found) == 1                     # gộp về một người
    assert found[0].minutes == max(x.minutes for x in group)


def test_answer_never_empty(db):
    for q in ["", "  ", "giúp mình với", "so sánh"]:
        res = answer_question(db, q)
        assert res["answer"].strip()


# ------------------------------------------------ các dạng câu hỏi mở rộng ----
def test_new_intents_are_recognised(db):
    """Mỗi dạng câu hỏi mới phải ra đúng chủ đề, không rơi về trợ giúp."""
    cases = [
        ("Ai đá penalty?", ("phạt đền", "penalty")),
        ("Cầu thủ nào đáng tiền nhất?", ("Đáng tiền", "điểm/triệu")),
        ("Ai có ceiling cao nhất?", ("Trần điểm", "ceiling")),
        ("Lựa chọn an toàn là ai?", ("an toàn", "rủi ro")),
        ("Hậu vệ nào giữ sạch lưới tốt?", ("sạch lưới",)),
        ("Cầu thủ nào nhiều người chọn nhất?", ("sở hữu", "template")),
        ("Ai đang được mua nhiều nhất?", ("mua nhiều",)),
        ("Có Double Gameweek nào không?", ("Gameweek", "Blank", "Double")),
        ("Luật tính điểm thế nào?", ("Luật tính điểm",)),
        ("Defensive Contribution là gì?", ("Defensive Contribution",)),
        ("Cách dùng web này?", ("Cách dùng", "Team ID")),
    ]
    for question, needles in cases:
        answer = answer_question(db, question)["answer"]
        assert any(n in answer for n in needles), f"{question} -> {answer[:80]}"


def test_common_vietnamese_words_not_read_as_team_codes(db):
    """"tốt" bỏ dấu thành "tot" trùng mã Tottenham — không được cướp ý định."""
    answer = answer_question(db, "Tiền đạo nào tốt nhất dưới 7 triệu?")["answer"]
    assert "tiền đạo" in answer.lower()
    assert "£7.0m" in answer
    assert "Spurs" not in answer


def test_team_specific_questions(db):
    from app.models import Team

    team = db.scalars(select(Team)).first()
    players_ans = answer_question(db, f"Cầu thủ {team.name} nào tốt nhất?")["answer"]
    fixtures_ans = answer_question(db, f"Lịch {team.name} thế nào?")["answer"]
    assert team.name in players_ans or "chưa" in players_ans
    assert team.name in fixtures_ans or "chưa" in fixtures_ans


def test_player_name_beats_team_name(db):
    """Hỏi kèm cả tên cầu thủ lẫn tên đội thì trả lời về cầu thủ."""
    from app.models import Player, Team

    p = db.scalars(
        select(Player).where(Player.minutes > 0).order_by(Player.total_points.desc())
    ).first()
    team = db.get(Team, p.team_id)
    answer = answer_question(db, f"{p.web_name} của {team.name} thế nào?")["answer"]
    assert p.web_name in answer
    assert "xP" in answer
