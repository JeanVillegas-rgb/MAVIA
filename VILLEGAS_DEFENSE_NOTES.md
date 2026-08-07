# MAVIA Defense Notes - Villegas Part

## Assigned Tasks

My assigned part focuses on converting teacher-uploaded learning materials into usable learning content for visually impaired learners.

The two main tasks are:

1. Upload lesson materials in PDF format, use an LLM to extract text, generate image or chart descriptions, and save the result to the database in JSON format.
2. Upload the course outline in PDF format and map the generated content to the correct module or topic. After confirmation, the system can create a playlist of audio lessons and questions.

## Purpose of This Feature

The purpose of this feature is to help teachers transform PDF-based lesson materials into accessible digital learning objects.

Instead of manually copying lessons, the teacher can upload a PDF. The system reads the document, identifies the meaningful instructional parts, and prepares them as learning objects. These learning objects can later be converted into audio lessons for visually impaired learners.

The goal is not to rewrite the teacher's lesson. The system should preserve the teacher's original lesson content as much as possible. The LLM is mainly used to decide which parts are valid learning objects and to describe visual content that cannot be directly read by screen readers.

## Main Workflow

The workflow starts when the teacher uploads a lesson PDF under a selected module or topic.

1. The teacher selects a topic.
2. The teacher uploads the lesson material PDF.
3. The backend extracts text from the PDF.
4. The system separates meaningful learning content from non-learning content.
5. The LLM helps identify which sections are suitable as learning objects.
6. If the PDF contains meaningful images, diagrams, tables, or charts, the system generates descriptions for them when possible.
7. The extracted learning objects are saved in the database.
8. The teacher reviews the generated learning objects.
9. The teacher can add, edit, delete, or confirm learning objects.
10. Confirmed learning objects are used to generate audio lessons and questions.

## What Counts as a Learning Object

A learning object is a section of content that a learner can study and understand as part of the lesson.

Examples of valid learning objects:

- A concept explanation
- A definition with supporting details
- A topic or subtopic discussion
- An example that teaches a concept
- A guided practice section if it helps the learner apply the lesson
- A chart, table, or image description if it contains instructional information

Examples of content that should not become learning objects:

- Page labels
- Repeated headers or footers
- Lesson title repeated without explanation
- Learning objectives list, unless the teacher wants it included
- Document metadata
- Instructions that do not teach the lesson content
- Empty headings with no useful explanation

## Important Design Decision

The LLM should not summarize or simplify the teacher's lesson text.

The expected behavior is:

Teacher PDF content in -> extracted learning object content out.

This is important because visually impaired learners should hear what the teacher provided, not a shortened or changed version of the lesson. The system may clean formatting, remove repeated headers, and organize sections, but it should not change the meaning or remove important details.

## PDF Text Extraction

The system uses PDF text extraction to read the content from uploaded files. Text is extracted from the pages and grouped into sections.

The challenge is that PDFs do not always store text in a simple reading order. Some PDFs contain:

- Repeated page titles
- Headers and footers
- Multi-column layout
- Tables
- Charts
- Images
- Section headings without paragraphs
- Text blocks that are visually connected but separated internally

Because of this, the extraction process needs both rule-based cleanup and LLM-based judgment.

## Role of the LLM

The LLM is used as an assistant for content understanding.

Its role is to:

- Identify whether extracted text is meaningful learning content
- Detect if a section is only a header, instruction, or metadata
- Help classify content into learning objects
- Generate image, chart, or diagram descriptions when visual content is detected
- Help map extracted lesson content to the correct topic or module

The LLM should not:

- Invent new lesson content
- Hardcode topic names
- Rewrite the lesson into a different script
- Remove important teacher-provided details
- Treat every extracted text block as a learning object

## Image, Chart, and Table Handling

For visually impaired learners, images and charts need text descriptions because they cannot rely on visual inspection.

When the PDF contains images, diagrams, or charts, the system attempts to detect meaningful visual elements. If a meaningful image is found, a vision-capable model can generate a description.

Tables may be extracted as text if the PDF stores them as selectable text. If the table is stored as an image, it may need image-based description. This is why teacher review is still important.

The goal is to preserve instructional information from visual materials by converting it into text that can later be read aloud.

## JSON and Database Storage

After extraction, the generated content is saved in the database in a structured format.

The saved data can include:

- Title of the learning object
- Extracted lesson content
- Source page or source order
- Topic or module association
- Instructional role or content type
- Image or chart description, if available
- Review status
- Confirmation status

This structured format allows the system to display learning objects in the teacher review page and reuse them for audio generation, question generation, and learner-path generation.

## Teacher Review

Teacher review is necessary because PDF extraction is not always perfect.

The system gives the teacher control before the content is used by learners. The teacher can:

- Add missing learning objects
- Edit extracted content
- Delete incorrect learning objects
- Confirm approved learning objects
- Regenerate extraction if needed
- Delete an uploaded file if it is wrong

This makes the feature safer because the teacher remains the final validator of the lesson content.

## Course Outline Mapping

The second part of the feature is uploading a course outline PDF.

The course outline is used to understand the structure of the subject. It may contain:

- Modules
- Topics
- Subtopics
- Lesson sequence
- Learning coverage

After the course outline is uploaded, the system can map generated learning objects to the correct module or topic.

This helps organize lessons properly instead of storing uploaded PDFs as unrelated files.

## Module-Based Learning Path

Learning objects are organized by module.

If several topics or subtopics belong to the same module and have confirmed learning objects, they can be combined into a module-based learner path.

The learner path should connect related learning objects without creating loops. The graph helps show how concepts are related and how a learner may move through the module.

There are two kinds of relationships:

- Prerequisite relationships, where one learning object must be understood before another
- Topic-parent or structural relationships, where learning objects are connected because they belong to the same topic or module

This is important because not every related lesson is a strict prerequisite. Some are connected because they are part of the same topic group.

## Audio Playlist

After learning objects are confirmed, the system can generate an audio playlist.

The playlist is based on the teacher-approved learning objects. Each item can become an audio lesson that visually impaired learners can listen to.

The purpose of the playlist is to make the lesson accessible in a sequence that follows the module or topic structure.

## Question Generation

After the learning objects are confirmed, the system can also generate questions and answers based on the lesson content.

Questions should be generated from the confirmed learning objects, not from rejected or deleted content.

This keeps the assessment aligned with what the learner actually studied.

## Why This Feature Is Important

This feature supports accessibility.

Many lesson materials are still distributed as PDFs, which may not be fully accessible to visually impaired learners. By extracting text, describing visual content, organizing learning objects, and generating audio lessons, the system helps convert teacher materials into a format that learners can hear and navigate.

It also helps teachers save time because they do not need to manually recreate all lesson materials from scratch.

## Possible Limitations

The system still depends on the quality of the uploaded PDF.

Possible limitations include:

- Scanned PDFs may need OCR before text can be extracted properly.
- Some tables may lose their original structure during extraction.
- Some charts may not be detected if they are embedded in unusual formats.
- The LLM may still need teacher review to avoid including non-learning content.
- Long PDFs may require more processing time.
- Local models like Llama or Gemma may run slowly depending on the computer's hardware.

Because of these limitations, the system includes teacher review before final confirmation.

## How I Can Explain It During Defense

My part handles the conversion of uploaded teacher PDFs into accessible learning content.

When a teacher uploads a lesson material, the system extracts the PDF text, detects meaningful lesson sections, and uses the LLM to help decide which parts are suitable as learning objects. It avoids using page labels, repeated titles, metadata, and other non-instructional text as learning objects.

For visual elements like charts, diagrams, or images, the system attempts to generate text descriptions so visually impaired learners can still understand the information. The extracted content is stored in the database in structured JSON format and shown to the teacher for review.

The teacher can edit, delete, add, or confirm the learning objects. Only confirmed learning objects are used for audio playlist generation and question generation.

For the course outline, the system uses the uploaded outline to organize the generated content under the correct module or topic. This allows the learner path to be formed by module, where confirmed learning objects from related topics can be connected and used as a structured audio learning path.

## Short Defense Answer

My feature allows teachers to upload PDF lesson materials and course outlines. The system extracts the lesson text, uses an LLM to identify valid learning objects, generates descriptions for meaningful visual content, and saves the result in the database as structured data. The teacher can review and confirm the content before it is used for audio lessons, question generation, and module-based learner paths. This helps make PDF learning materials more accessible for visually impaired learners.

## Technical Summary

The backend handles PDF upload, text extraction, content generation, learning-object storage, and mapping to topics or modules.

The frontend displays the extracted learning objects and provides teacher controls for review.

The database stores uploaded materials, extracted learning objects, generated descriptions, review status, and relationships used for the learner path.

The LLM supports content classification and visual description, while the teacher remains responsible for final validation.

## Key Point to Remember

The system should preserve what the teacher uploaded.

The LLM should help identify and organize learning content, not replace the teacher's lesson with a simplified summary.

