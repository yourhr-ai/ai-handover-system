# `required` is retained for data compatibility, but report validation now requires
# any one of the five answers rather than validating individual questions.
HANDOVER_QUESTIONS = [
    {
        "title": "후임자가 인수인계 직후 가장 먼저 해야 할 일은 무엇인가요?",
        "category_label": "최우선 처리",
        "placeholder": "① A사 수정 견적서를 금요일까지 발송\n② 7월 급여자료를 세무사에게 전달\n③ 다음 주 회의 전에 매출자료 업데이트",
        "required": True,
    },
    {
        "title": "후임자가 실수하지 않으려면 반드시 주의해야 할 것은 무엇인가요?",
        "category_label": "주의사항",
        "placeholder": "① A사에는 가격표를 바로 보내지 말고 팀장 확인 후 전달\n② 월말 자료는 수식이 깨질 수 있으므로 값만 붙여넣으면 안 됨\n③ 대표 승인 전에는 고객에게 일정을 확정해서 안내하면 안 됨",
        "required": True,
    },
    {
        "title": "정기적으로 반복하는 업무는 언제, 어떤 순서로 처리하나요?",
        "category_label": "반복 업무",
        "placeholder": "매월 1일 매출자료 다운로드\n→ 거래처별 실적 확인\n→ 전월 자료와 비교\n→ 이상 수치 확인\n→ 매월 3일까지 팀장에게 보고",
        "required": True,
    },
    {
        "title": "평소와 다르게 처리해야 하거나 혼자 판단하면 안 되는 경우는 무엇인가요?",
        "category_label": "협업 필요",
        "placeholder": "① 고객이 환불을 요구하면 바로 답변하지 말고 김 팀장에게 확인\n② 계약금액이 1,000만 원을 넘으면 대표 승인 후 진행\n③ 납기가 늦어질 것 같으면 생산팀 확인 후 고객에게 안내\n④ 시스템 오류는 박 대리에게 문의",
        "required": True,
    },
    {
        "title": "기타 후임자가 알아야 할 내용을 작성해 주세요.",
        "category_label": "기타",
        "placeholder": "",
        "required": False,
    },
]
