from django.conf import settings
from django.db import models
from lessons.models import CourseGroup

VARIANTS = [("standard", "Standard"), ("simplified", "Simplified"), ("elaborated", "Elaborated")]

#Enrollment first cuz if no enroll then no see package okay?
class Enrollment(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enrollments", limit_choices_to={"role":"STUDENT"})
    course = models.ForeignKey(CourseGroup, on_delete=models.CASCADE, related_name="enrollments")
    created_by= models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="+", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "course")
        ordering = ["created_at", "id"]


    def __str__(self):
        return f'Student: {self.student} in Course {self.course}.'


#the BKT mastery estimate for how well a student knows a concept--used later to determine which variant of a step to show the student 
#this is forda callibration phase where--the scores of the student for each question is computed and logged BUT is not used to gate anything
#we do not score gate sa calibration phase because we are handling the cold start--where the environment needs to learn the student and their current knowedlge or capability first 
class ConceptMastery(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="concept_mastery")    
    concept_id = models.PositiveIntegerField() #step[concept_id] from topic_package.steps
    mastery_score = models.FloatField() #starts from the student's baseline L0 (+ prerequisites), set by the engine
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "concept_id")

    def __str__(self):
        return f"Mastery score of {self.student.username} on concept {self.concept_id}: {self.mastery_score}, updated at {self.updated_at}"



#the baseline will show HOW the student performs overall in a course -- starts on population weights(which are the default weights we use por everywan that are decided on initial design but can be recalibrated appropriately)
class StudentBaseline(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="baselines")    
    course = models.ForeignKey(CourseGroup, on_delete=models.CASCADE, related_name="+")
    total_responses_count= models.PositiveIntegerField(default=0)
    total_correct_count = models.PositiveIntegerField(default=0)
    first_attempts_count = models.PositiveIntegerField(default=0) #how many concepts the student has answered so far--the "n" for estimating starting knowledgez
    first_attempts_correct_count = models.PositiveIntegerField(default=0) #how many of those first answers were correct
    estimated_ability = models.FloatField() #how often they asnwer corerectly (total correct count / total responses count)
    estimated_l0 = models.FloatField() #L-zero (L0): what they know of a concept after the first run
    calibrated = models.BooleanField(default=False) #trot once the 95% margin of error is within .15--flags if the baseline is trustworthy naw or not
    calibrated_at = models.DateTimeField(null=True, blank=True) #tells us later when the students calibrated after how many answers over how many minutes 
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "course")

    def __str__(self):
        state = "Calibrated" if self.calibrated else f"Calibrating ({self.total_responses_count} responses so far.)"
        return f"Student {self.student.username} baseline for course {self.course.title}: {state}. Estimated ability: {self.estimated_ability:.2f}, L0: {self.estimated_l0:.2f}, Updated at: {self.updated_at}"


#the student and app interaction saved and logged for the engine later to learn from
class Decision(models.Model):
    class Action(models.TextChoices):
        NEXT_QUESTION = "next_question", "Next question in the concept"
        RETRY = "retry", "Asked again"
        ESCALATE_VARIANT = "escalate_variant", "Re-taught with another explanation type"
        REGRESS = "regress", "Sent back through a prerequisite concept"
        RESUME = "resume", "Resumed the topic after the prerequisite"
        ADVANCE = "advance", "Moved on to the next concept"
        COMPLETE = "complete", "Completed the topic"

    response = models.OneToOneField("mobile_course_package.StudentResponse", on_delete=models.CASCADE, related_name="decision")

    #the state the engine saw
    predicted_correct = models.FloatField() #BKT's P(correct) BEFORE the answer -- checks the model's accuracy later
    mastery_before = models.FloatField()
    mastery_after = models.FloatField()
    p_guess_used = models.FloatField() #depends on the question format (TF / MCQ)
    baseline_ability = models.FloatField() #the student's baseline at that moment
    baseline_calibrated = models.BooleanField()

    #where the student was when the engine decided -- saved as it was then, since progress gets
    #overwritten on the next answer. Logging only: nothing reads these yet (phase 2 learns from them)
    step_position = models.PositiveIntegerField(null=True, blank=True)
    attempt_number = models.PositiveIntegerField(default=1) #which try at this question
    misses_on_question = models.PositiveIntegerField(default=0) #wrong answers on this question so far, this one included
    questions_left_in_step = models.PositiveIntegerField(null=True, blank=True) #still worth asking in the step after this answer
    prerequisite_mastery = models.FloatField(null=True, blank=True) #average mastery of the step's prerequisites met so far (None: none)
    on_detour = models.BooleanField(default=False) #answered while reviewing a prerequisite
    step_already_regressed = models.BooleanField(default=False) #this step already used its one detour
    action_probability = models.FloatField(default=1.0) #how likely the policy was to pick this action -- the rules always do: 1.0

    #what it decided
    action = models.CharField(max_length=24, choices=Action.choices)
    next_step_position = models.PositiveIntegerField(null=True, blank=True) #None once completed
    next_variant = models.CharField(max_length=10, choices=VARIANTS, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.response.student} Q{self.response.question_id} -> {self.action} (mastery {self.mastery_before:.2f} -> {self.mastery_after:.2f})"

