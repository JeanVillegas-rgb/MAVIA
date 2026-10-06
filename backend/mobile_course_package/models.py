from django.db import models
from django.core.exceptions import ValidationError
from lessons.models import OutlineNode
from django.conf import settings

VARIANTS = [("standard", "Standard"), ("simplified", "Simplified"), ("elaborated", "Elaborated")]
# Create your models here.


#package content is built once per publish (live from teacher web side), which is used by every student



#The consumable package per topic pulled from the learning path
class TopicPackage(models.Model):
    topic = models.OneToOneField(OutlineNode, on_delete=models.CASCADE, related_name="topic_package")
    published_at = models.DateTimeField()
    steps = models.JSONField(default=list)  # list of dicts, each dict is a step with questions and content
    answer_key = models.JSONField(default=dict)  # dict of question_id -> answer WHICH are never sent to the student
    built_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Topic Package for {self.topic.title}. Built at {self.built_at}"



#student tracker per topic
class TopicPackageProgress(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="topic_progress")    
    topic = models.ForeignKey(OutlineNode, on_delete=models.CASCADE, related_name="+") #no reverse relation
    current_step_position = models.PositiveIntegerField(default = 1) #index of the current step in the step list (topic_package.steps)
    current_variant = models.CharField(max_length =10, choices=VARIANTS, default = "standard") 
    return_to_position = models.PositiveIntegerField(null=True, blank=True) #if the student is sent back to a prerequisite concept, this is where they will return to after completing it
    regressed_positions = models.JSONField(default=list, blank=True) #list of positions the student has been sent back to (prerequisites) so we can track how many times they have been sent back and to which concepts
    completed = models.BooleanField(default=False)
    # The question the last command asked for (None: listen, then continue). Reopening
    # the topic asks exactly this one, so the app never picks a different question
    # from the one the engine decided on.
    pending_question_id = models.PositiveBigIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "topic")

    def __str__(self):
        status = f"completed at {self.updated_at}" if self.completed else "not yet completed"
        return f"Progress for {self.student.username} on {self.topic.title}: step {self.current_step_position}, {self.current_variant}, {status}"


class StudentResponse(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="responses")    
    topic = models.ForeignKey(OutlineNode, on_delete = models.CASCADE, related_name="+") #no reverse relation
    concept_id = models.PositiveBigIntegerField() #step[concept_id] from topic_package.steps to track which concept mastery to update -> for adaptive
    question_id = models.PositiveBigIntegerField() #step[question_id] from topic_package.steps
    selected_answer = models.CharField(max_length=20) #Student Answer selected (Limited to A B C D)
    is_correct = models.BooleanField() 
    variant = models.CharField(max_length=10, choices=VARIANTS, default="standard")
    attempt_number = models.PositiveIntegerField(default=1)
    question_format = models.CharField(max_length=5) #mcq or tf
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


    def __str__(self):
        return f"Student {self.student} answered question {self.question_id} in topic {self.topic} with answer {self.selected_answer}. Is correct: { 'O' if self.is_correct else 'X'}. Created at: {self.created_at}"




