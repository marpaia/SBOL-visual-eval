"""Constants describing the SBOL Visual retrospective study and its sources."""

RETROSPECTIVE_DOI = "10.1021/acssynbio.5c00417"


RUBRIC_URL = (
    "https://docs.google.com/document/d/"
    "1vK8DdeN5QbFqKy0h2uLIuuB89hU_Zlj_o41NX1BVtAY/export?format=txt"
)
SBOL_VISUAL_3_URL = "https://sbolstandard.org/docs/SBOL-Visual-3.0.pdf"
ANNIVERSARY_ROOT = "https://sbolstandard.org/sbolv-10-years/"


YEARS = tuple(range(2012, 2024))
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTH_NUMBER = {month: index for index, month in enumerate(MONTHS, start=1)}
YEAR_SUPPLEMENT = {year: year - 2010 for year in YEARS}  # 2012 -> s002, 2023 -> s013


USER_AGENT = "SBOL-visual-eval/0.1 (research corpus builder)"
