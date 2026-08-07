import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from lessons.services.outline_parser import parse_outline_text

sample_text = '''COURSE OUTLINE
Grade 1 - Chemistry & Biology
Course Information
Course Title: Teaching Science in the Elementary Grades (Chemistry and Biology)
Course Code: Sci 1
Credit Units: 3 units
Prerequisite: None
Course Description: An introductory professional-education course that builds future teachers' grasp of
core Chemistry and Biology concepts as they appear across the elementary grade levels, while
developing skill in planning lessons, designing instructional materials, and assessing science learning. The Chemistry strand covers the properties, structure, and changes of matter; the Biology strand covers
plant and animal structures and functions, heredity and variation, biodiversity and evolution, and
ecosystems. Intended Learning Outcomes
By the end of the course, students should be able to:
- Explain the K to 12 science curriculum framework and its spiral progression
- Demonstrate mastery of foundational Chemistry and Biology concepts at the elementary level
- Apply a range of teaching strategies suited to elementary science instruction
- Design instructional materials appropriate to specific topics and grade levels
- Use technology to support creative and innovative science teaching
- Select and apply suitable tools for assessing student learning in science
- Model scientific inquiry and sound professional values while teaching science
Weekly Course Outline
Week Topics / Content Learning Focus Activities / Output
Week 1
Classroom Orientation (Setting
of expectation, Self-introduction, Classroom Policies, VMGO,
Introduction to the Course
syllabus)
Introduction to the course:
Teaching Science in the
Elementary Grades: An Overview
* Lesson 1: The Science
Framework in K to 12
* Lesson 2: Contents of
Elementary Science in a Spiral
Progress
* Lesson 3: The Teaching of
Science in the Elementary
Grades
Week 2
Module 1: Properties of Matter
* Lesson 1: Solid, Liquid and
Gas
* Lesson 2: Grouping Materials
Based on Properties
Grade 3-4 entry points:
solids/liquids/gases;
classifying materials by
properties
Hands-on sorting activity;
worksheet
Week 3
Module 1: Properties of Matter
* Lesson 3: Physical and
Chemical Properties of
Matter: Useful and Harmful
Materials
* Lesson 4: Mixtures and
Their Characteristics
Grade 5-6 entry points:
distinguishing useful/harmful
materials; describing mixtures
Small-group
reporting; concept
map
Week 4
Module 2: Changes that Materials
Undergo
* Lesson 1: Changes that
Materials Undergo
* Lesson 2: Changes that
Materials Undergo: Useful and
Harmful
Grade 3-4 entry points: identifying
changes; useful vs. harmful effects
Group
discussion;
worksheet
Weeks
5-6
Module 2: Changes that Materials
Undergo
* Lesson 3: Changes that
Materials Undergo due to
Oxygen and Heat
* Lesson 4: Separating Mixture
Grade 5-6 entry points: causes of
change; separation techniques
Demonstration; lab-style worksheet
Week 7
(2nd
Trinal)
Module 1: Parts and Functions of
Human Being
* Lesson 1: Human Sense
Organs
* Lesson 2: Human Major
Body Organs
Grade 3-4 entry points: sense
organs and their care; major
body organs
Group presentation
Week 8
Module 1: Parts and Functions of
Human Being
* Lesson 3: Male and
Female Reproductive
Systems
* Lesson 4: The Human
Organ System at Work
Grade 5-6 entry points:
reproductive system
(age-appropriate); organ
systems working together
Short research task
on medical
advances
Week 9
Module 2: Heredity, Inheritance
and Variations
* Lesson 1: Living Things
Reproduce
* Lesson 2: Life Cycles of
Human, Animals and Plants
Grade 3-4 entry points: how living
things reproduce; life cycles
Group
discussion;
illustration
Week 10
Module 2: Heredity, Inheritance
and Variations
* Lesson 3: Reproduction
Among Flowering Plants
* Lesson 4: Reproduction in
Non-Flowering Plants
Grade 5-6 entry points:
flowering plant reproduction;
variation among organisms
Group discussion
Week 11
Module 3: Biodiversity and
Evolution
* Lesson 1: Animals and Plants:
Parts, Functions and Importance
to Humans
* Lesson 2: Plants and Animals in
their Habitats
Grade 3-4 entry points: plant and
animal parts, purposes, and
environmental adaptations
Group discussion;
classification
activity
Week 12
Module 3: Biodiversity and
Evolution
* Lesson 3: Reproductive
Structures of Animals and Plants
* Lesson 4: Common
Characteristics of Animals and
Plants for Classification
Grade 5-6 entry points: structural
classification and reproductive
anatomy of animals and plants
Peer review session;
classification matrix
Week 13
(3rd
Trinal)
Module 4: Ecosystem
* Lesson 1: Living things
depend on their environment
for basic needs
* Lesson 2: Beneficial and
Harmful Interactions among
living things
Grade 3-4 entry points: living
things and their environment;
beneficial/harmful interactions
Research presentation on
a natural cycle
Week 14
Module 4: Ecosystem
Lesson 3: Interactions in estuaries
and Intertidal Zones
* Lesson 4: Interactions Among
living things in Coral Reefs and
Tropical Rainforest
Grade 5-6 entry points: estuaries,
intertidal zones, coral reefs, rainforests
Community-based
project proposal
Week 15
Applying the Content: Lesson
planning practice (Chemistry
strand, Grades 3-4)
Translating spiral-curriculum
content into a classroom lesson
plan
Draft lesson plan
Week 16
Applying the Content: Lesson
planning practice (Chemistry
strand, Grades 5-6)
Continued lesson-plan
development and peer feedback
Draft lesson plan
Week 17
Applying the Content: Lesson
planning practice (Biology strand, Grades 3-4)
Translating human-body and
ecological content into a classroom
lesson plan
Draft lesson plan
Week 18
Applying the Content: Lesson
planning practice (Biology strand, Grades 5-6); Course wrap-up
Finalizing lesson plans; portfolio
compilation
Final lesson plan portfolio
'''

from lessons.services.outline_parser import _build_outline_candidates, _extract_module_lesson_bullet_outline

candidates = _build_outline_candidates(sample_text, limit=200)
print('\n[DEBUG] Candidates:')
for c in candidates[:50]:
    print(c)

module_nodes = _extract_module_lesson_bullet_outline(sample_text)
print('\n[DEBUG] Module-lesson extractor result:')
for node in module_nodes:
    print('MODULE:', node.title, 'children:', [c.title for c in node.children])

parsed = parse_outline_text(sample_text)


def dump(node, indent=0):
    print('  ' * indent + '- ' + node.title)
    for c in node.children:
        dump(c, indent + 1)

print('\n[DEBUG] Parsed outline:')
for root in parsed:
    dump(root)

print('\n[SUMMARY] Root titles:', [n.title for n in parsed])
