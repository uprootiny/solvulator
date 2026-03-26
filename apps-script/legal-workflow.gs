// --- CONFIGURATION ---
// NOTE: API key should be in Script Properties, not hardcoded
// ScriptProperties.getProperty('GEMINI_API_KEY')
const GEMINI_API_KEY = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY') || '';
const DRIVE_FOLDER_ID = '1FG27TPmB0LcVl-c7hBcZiFC3aqVk2u8b';
const SHEET_ID = '1cK4F7_5gGB_inEhZieiZdrdVbZOZWqE2phpHEO2WStM';
const PROCESSED_LABEL_NAME = 'Legal_AI_Processed';

function mainLegalWorkflow() {
  console.log("Starting mainLegalWorkflow...");

  const sheet = SpreadsheetApp.openById(SHEET_ID).getActiveSheet();
  const folder = DriveApp.getFolderById(DRIVE_FOLDER_ID);

  let label = GmailApp.getUserLabelByName(PROCESSED_LABEL_NAME);
  if (!label) {
    label = GmailApp.createLabel(PROCESSED_LABEL_NAME);
  }

  const threads = GmailApp.search('has:attachment -label:' + PROCESSED_LABEL_NAME, 0, 10);
  console.log(`Found ${threads.length} threads to process`);

  for (let i = 0; i < threads.length; i++) {
    const messages = threads[i].getMessages();

    for (let j = 0; j < messages.length; j++) {
      const message = messages[j];
      const subject = message.getSubject();
      const body = message.getPlainBody();

      const isLegal = checkIfLegal(subject, body);

      if (isLegal) {
        const attachments = message.getAttachments();

        for (let k = 0; k < attachments.length; k++) {
          const attachment = attachments[k];

          if (attachment.getContentType() === 'application/pdf') {
            const pdfFile = folder.createFile(attachment);
            const pdfUrl = pdfFile.getUrl();

            const extractedText = extractTextFromPDF(pdfFile.getId());

            const analysisData = analyzeLegalContent(extractedText);

            if (analysisData) {
               writeToSheet(sheet, analysisData, pdfUrl);
            } else {
               console.error("AI Analysis failed for: " + subject);
            }
          }
        }
      }
    }
    threads[i].addLabel(label);
  }

  console.log("Workflow completed.");
}

// --- HELPER FUNCTIONS ---

function checkIfLegal(subject, body) {
  const prompt = `Analyze this email. Is it related to a legal affair, court case, lawsuit, or legal dispute? Reply ONLY with "YES" or "NO".\n\nSubject: ${subject}\nBody: ${body}`;
  const response = callGeminiAPI(prompt);
  return response.includes("YES");
}

function extractTextFromPDF(fileId) {
  const file = DriveApp.getFileById(fileId);
  const resource = {
    title: file.getName(),
    mimeType: 'application/pdf'
  };

  const tempDoc = Drive.Files.insert(resource, file.getBlob(), {ocr: true, ocrLanguage: 'he'});
  const doc = DocumentApp.openById(tempDoc.id);
  const text = doc.getBody().getText();

  Drive.Files.remove(tempDoc.id);

  return text.substring(0, 150000);
}

function analyzeLegalContent(text) {
  const prompt = `
    Analyze the following legal document text.
    Return the analysis STRICTLY as a JSON object with the following keys, and no markdown formatting outside the JSON:
    - case_number (string)
    - case_name (string)
    - plaintiff (string)
    - defendant (string)
    - plaintiff_lawyer (string)
    - defendant_lawyer (string)
    - case_type (string)
    - status (string)
    - decision (string)
    - defendant_deadline (string)
    - plaintiff_deadline (string)
    - plaintiff_strategy (string: detailed legal strategy based on text)
    - defendant_strategy (string: detailed legal strategy based on text)
    - exhibits_count (number: how many exhibits/attachments are referenced)
    - exhibits (array of objects with 'description' and 'estimated_page_number')
    - facts (array of objects with 'fact_description' and 'estimated_page_number')

    Document Text:
    ${text}
  `;

  const responseText = callGeminiAPI(prompt);
  try {
    const jsonString = responseText.replace(/```json/g, '').replace(/```/g, '').trim();
    return JSON.parse(jsonString);
  } catch (e) {
    console.error("Failed to parse JSON from AI: " + responseText);
    return null;
  }
}

function callGeminiAPI(prompt) {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${GEMINI_API_KEY}`;

  const payload = {
    "contents": [{
      "parts": [{"text": prompt}]
    }]
  };

  const options = {
    "method": "post",
    "contentType": "application/json",
    "payload": JSON.stringify(payload),
    "muteHttpExceptions": true
  };

  const response = UrlFetchApp.fetch(url, options);
  const json = JSON.parse(response.getContentText());

  if (json.candidates && json.candidates.length > 0) {
    return json.candidates[0].content.parts[0].text;
  }
  return "";
}

function writeToSheet(sheet, data, pdfBaseUrl) {
  const rowData = [
    data.case_number || "N/A",
    data.case_name || "N/A",
    data.plaintiff || "N/A",
    data.defendant || "N/A",
    data.plaintiff_lawyer || "N/A",
    data.defendant_lawyer || "N/A",
    data.case_type || "N/A",
    data.status || "N/A",
    data.decision || "N/A",
    data.plaintiff_deadline || "N/A",
    data.defendant_deadline || "N/A",
    data.plaintiff_strategy || "N/A",
    data.defendant_strategy || "N/A",
    data.exhibits_count || 0
  ];

  if (data.exhibits && Array.isArray(data.exhibits)) {
    data.exhibits.forEach(ex => {
      rowData.push(ex.description);
      rowData.push(`${pdfBaseUrl}#page=${ex.estimated_page_number || 1}`);
    });
  }

  if (data.facts && Array.isArray(data.facts)) {
    data.facts.forEach(fact => {
      rowData.push(fact.fact_description);
      rowData.push(`${pdfBaseUrl}#page=${fact.estimated_page_number || 1}`);
    });
  }

  sheet.appendRow(rowData);
}
