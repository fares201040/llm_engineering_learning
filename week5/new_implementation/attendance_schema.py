"""Stable offline ingestion field lists.

The online runtime now learns its physical query schema from PostgreSQL. Keeping these
two tuples here avoids changing the offline ingestion record/chunk format.
"""

_BASE_FIELDS = (
    "Employee_ID",
    "Name",
    "Organization_Unit",
    "Country",
    "Work_Location",
    "Department",
    "Position",
    "Job",
    "Gradeset",
    "Grade",
    "Date",
    "Day",
    "Day_Type",
    "Holiday_Type",
    "Shift",
    "Status",
    "Exception",
    "Total_Worked_Hrs",
    "Lateness_Hrs",
    "Early_Out_Hrs",
    "Overbreak_Hrs",
    "Regular_Units",
    "pre_ot_hrs",
    "Post_OT_hrs",
    "Total_OT",
    "OT_Authorized",
    "OT_Not_Authorized",
    "Leave_Type",
    "Leave_Hrs",
    "chunk_type",
    "Period",
)

_OVERTIME_FIELDS = tuple(
    field
    for index in range(1, 6)
    for field in (f"OT_Type_{index}", f"OT_Value_{index}")
)

_LIVE_FIELDS = (
    "Actual_From_Date",
    "Actual_From_Time",
    "Actual_To_Date",
    "Actual_To_Time",
    "Employee_Remarks",
    "From_Date",
    "From_Time",
    "Pending_with",
    "Post_OT_End_Time",
    "Post_OT_Start_Time",
    "Pre_OT_End_Time",
    "Pre_OT_Start_Time",
    "Schedule_From_Date",
    "Schedule_From_Time",
    "Schedule_To_Date",
    "Schedule_To_Time",
    "To_Date",
    "To_Time",
    "last_Updated_date",
)

SEARCHABLE_FIELDS = _BASE_FIELDS + _OVERTIME_FIELDS + _LIVE_FIELDS
METADATA_FIELDS = SEARCHABLE_FIELDS

__all__ = ["METADATA_FIELDS", "SEARCHABLE_FIELDS"]
