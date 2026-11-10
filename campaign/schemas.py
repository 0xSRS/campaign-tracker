"""Typed definitions for the campaign and its required courses."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CourseDefinition(BaseModel):
    """One course a student must complete.

    template_id is the course TEMPLATE id. It is NOT assumed to equal the id
    of the credential awarded on a public profile. That mapping must be
    verified against real profile data in the scraper module.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1)
    template_id: int = Field(gt=0)


class CampaignConfig(BaseModel):
    """The full campaign: a name and the list of required courses."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    campaign_name: str = Field(min_length=1)
    courses: list[CourseDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "CampaignConfig":
        ids = [c.template_id for c in self.courses]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate template_id found in courses")

        names = [c.name.casefold() for c in self.courses]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate course name found in courses")
        return self

    @property
    def required_count(self) -> int:
        """How many courses complete the campaign (never hardcode 10)."""
        return len(self.courses)

    @property
    def template_ids(self) -> set[int]:
        return {c.template_id for c in self.courses}