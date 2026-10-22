"""Small, traceable catalog for the building-slope teaching prototype."""

STANDARDS = {
    "GB 50330-2013": {
        "name": "建筑边坡工程技术规范",
        "url": "https://www.ndls.org.cn/standard/detail/853bfbf5def57ba420536385d7ac33e2",
        "role": "重力式挡墙抗滑、抗倾覆简化对照",
        "coverage": "已做教学模型对照；不是完整规范验算",
    },
    "GB 55003-2021": {
        "name": "建筑与市政地基基础通用规范",
        "url": "https://ebook.chinabuilding.com.cn/zbooklib/book/detail/show?SiteID=1&bookID=147869&isEnterprise=Y",
        "role": "地基基础相关要求",
        "coverage": "未验算：缺少地基承载力、变形等项目数据",
    },
    "GB 50007-2011": {
        "name": "建筑地基基础设计规范",
        "url": "https://ebook.chinabuilding.com.cn/zbooklib/bookpdf/probation?SiteID=1&bookID=61019",
        "role": "地基基础设计参考",
        "coverage": "未验算：缺少地基参数与基础设计数据",
    },
}


def selected_standard_report(selected: list[str], result: dict | None = None) -> list[dict]:
    report = []
    for code in selected:
        if code not in STANDARDS:
            raise ValueError(f"未知规范：{code}")
        item = {"code": code, **STANDARDS[code]}
        if code == "GB 50330-2013" and result is not None:
            item["checks"] = [
                {"item": "抗滑", "value": result["sliding_ratio"], "teaching_reference": 1.3, "passed": result["sliding_pass"]},
                {"item": "抗倾覆", "value": result["overturning_ratio"], "teaching_reference": 1.6, "passed": result["overturning_pass"]},
            ]
        report.append(item)
    return report
