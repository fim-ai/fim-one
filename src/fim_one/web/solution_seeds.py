"""Market Solution Templates — admin-importable seed content.

Contains 8 vertical Solution Templates (Agent + Skill bundles) that an admin
can import into the Market organisation on demand via the admin API endpoint
``POST /api/admin/market/import-templates``.

Templates are **not** auto-seeded on startup or first-admin registration.
The ``import_solution_templates()`` function uses upsert-by-name semantics:
existing agents are updated, new ones are created.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db.models.agent import Agent
from fim_one.db.models.skill import Skill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------


class _SkillDef(TypedDict):
    name: str
    description: str
    content: str


class _AgentDef(TypedDict):
    name: str
    description: str
    instructions: str
    execution_mode: str


class _TemplateDef(TypedDict):
    agent: _AgentDef
    skill: _SkillDef
    alias: str


# ---------------------------------------------------------------------------
# Template definitions (English)
# ---------------------------------------------------------------------------

SOLUTION_TEMPLATES: list[_TemplateDef] = [
    # 1. Financial Audit Assistant
    {
        "alias": "financial-audit",
        "agent": {
            "name": "Financial Audit Assistant",
            "description": (
                "A professional financial audit AI assistant that reviews financial reports, "
                "verifies calculation logic, identifies data anomalies and compliance risks, "
                "helping auditors improve efficiency and accuracy."
            ),
            "instructions": (
                "You are a senior financial audit expert AI assistant. Your core responsibility is to "
                "help auditors complete financial report review tasks.\n\n"
                "Working principles:\n"
                "1. Always approach every piece of financial data with professionalism and rigour\n"
                "2. Grade discovered anomalies and risks (High / Medium / Low) and provide specific review recommendations\n"
                "3. Cite specific accounting standards and regulatory provisions to support your analytical conclusions\n"
                "4. Remain objective and neutral — do not speculate; distinguish facts from assumptions\n"
                "5. Output structured audit reports containing findings, risk level, recommendations, and references"
            ),
            "execution_mode": "auto",
        },
        "skill": {
            "name": "Financial Report Review SOP",
            "description": (
                "A standardised financial report review process covering systematic review methods "
                "for the balance sheet, income statement, and cash flow statement."
            ),
            "content": (
                "# Financial Report Review Standard Operating Procedure\n\n"
                "## 1. Review Preparation\n"
                "1. Confirm review scope: clarify the statement types (balance sheet, income statement, cash flow statement) and the reporting period\n"
                "2. Gather base materials: obtain current-period and prior-period (at least two periods) financial statements along with related notes\n"
                "3. Understand industry context: confirm any special accounting treatment requirements for the auditee's industry\n\n"
                "## 2. Data Verification\n"
                "1. Cross-referencing checks: verify intra-statement relationships (e.g., Assets = Liabilities + Equity) and inter-statement relationships (e.g., net profit on the income statement ties to retained earnings on the balance sheet)\n"
                "2. Calculation re-performance: re-perform calculations for all computed line items, including subtotals, totals, and ratio metrics\n"
                "3. Data consistency: check whether the same data point is disclosed consistently across different statements and notes\n\n"
                "## 3. Anomaly Identification\n"
                "1. Trend analysis: compare multi-period data and flag unusual fluctuations (changes exceeding 20% require focused attention)\n"
                "2. Ratio analysis: compute key financial ratios (current ratio, quick ratio, debt-to-equity ratio, gross margin, etc.) and benchmark against industry averages\n"
                "3. Significant transactions: scrutinise large, unusual, or related-party transactions for adequate disclosure\n"
                "4. Accounting estimates: review items involving significant judgement (bad-debt provisions, depreciation policies, impairment testing, etc.)\n\n"
                "## 4. Compliance Review\n"
                "1. Accounting standard compliance: verify that accounting policies and disclosures comply with applicable standards (GAAP / IFRS)\n"
                "2. Disclosure completeness: confirm that all required note disclosures are present and sufficiently detailed\n"
                "3. Regulatory compliance: ensure the financial statements meet any special requirements of relevant regulatory bodies\n\n"
                "## 5. Report Output\n"
                "Output review results in the following format:\n"
                "- **Finding ID**: FIN-YYYY-NNN\n"
                "- **Risk Level**: High / Medium / Low\n"
                "- **Finding Description**: specific explanation of the issue\n"
                "- **Impact Analysis**: impact on the overall fair presentation of financial statements\n"
                "- **Recommended Actions**: items requiring further verification or adjustment\n"
                "- **Reference**: applicable accounting standard or regulatory provision"
            ),
        },
    },
    # 2. Contract Review Assistant
    {
        "alias": "contract-review",
        "agent": {
            "name": "Contract Review Assistant",
            "description": (
                "A professional contract review AI assistant that reads entire contracts, "
                "identifies risk clauses, flags legal pitfalls, and provides amendment suggestions. "
                "Suitable for commercial, procurement, employment, and other contract types."
            ),
            "instructions": (
                "You are an experienced legal contract review AI assistant. Your core responsibility is to "
                "help users review contract texts quickly and accurately.\n\n"
                "Working principles:\n"
                "1. Review contract clauses one by one, focusing on rights and obligations, breach penalties, and dispute resolution\n"
                "2. Flag and grade risk clauses (High Risk / Medium Risk / Low Risk) with specific risk explanations\n"
                "3. Provide concrete suggested amendment text rather than abstract, principled guidance\n"
                "4. Flag potentially missing important clauses (force majeure, confidentiality, IP ownership, etc.)\n"
                "5. Use a clear comparison format showing original text and suggested revisions side by side"
            ),
            "execution_mode": "auto",
        },
        "skill": {
            "name": "Contract Clause Review SOP",
            "description": (
                "A systematic contract review process covering risk identification and "
                "amendment suggestions for core contract clauses."
            ),
            "content": (
                "# Contract Clause Review Standard Operating Procedure\n\n"
                "## 1. Basic Contract Information Verification\n"
                "1. Confirm contracting parties: verify that the names, addresses, and legal/authorised representatives of all parties are complete and accurate\n"
                "2. Determine contract type: identify the contract nature (sale, service, lease, partnership, employment, etc.) and determine applicable laws and regulations\n"
                "3. Contract term: check effective conditions, validity period, renewal clauses, and termination conditions\n\n"
                "## 2. Core Clause Review\n"
                "1. **Subject matter clause**: verify that the contract subject is described clearly and specifically, and that quantity/quality standards are quantifiable and verifiable\n"
                "2. **Price and payment**: check pricing clauses (tax-inclusive/exclusive), payment methods, payment milestones, and invoicing terms\n"
                "3. **Delivery and acceptance**: review delivery timing, location, method, acceptance criteria, and acceptance procedures\n"
                "4. **Breach of contract**: evaluate whether penalty rates are reasonable (typically not exceeding 30% of contract value) and whether the scope of damages is clearly defined\n"
                "5. **Dispute resolution**: confirm whether the chosen jurisdiction or arbitration institution is favourable to your party\n\n"
                "## 3. Risk Clause Identification\n"
                "Focus on the following high-risk clauses:\n"
                "1. Exclusivity clauses: whether there are unreasonable exclusive/sole arrangements\n"
                "2. Auto-renewal clauses: whether the exit notice period is unreasonably short\n"
                "3. Unlimited liability clauses: whether there are uncapped liability commitments\n"
                "4. Unilateral amendment clauses: whether one party retains the right to unilaterally modify contract terms\n"
                "5. Non-compete clauses: whether the scope and duration of restrictions are reasonable\n"
                "6. Intellectual property clauses: whether IP ownership is clear and licensing scope is reasonable\n\n"
                "## 4. Missing Clause Check\n"
                "Confirm that the following essential clauses are included in the contract:\n"
                "- Force majeure clause\n"
                "- Confidentiality obligation clause\n"
                "- Notice and service clause\n"
                "- Assignment restriction clause\n"
                "- Entire agreement clause\n"
                "- Severability clause\n\n"
                "## 5. Review Report Output\n"
                "Output in the following format:\n"
                "- **Clause Location**: Article X, Section X\n"
                "- **Risk Level**: High / Medium / Low\n"
                "- **Original Text**: excerpt of the relevant clause\n"
                "- **Risk Explanation**: specific risk analysis\n"
                "- **Amendment Suggestion**: suggested revised clause text"
            ),
        },
    },
    # 3. Data Reporting Assistant
    {
        "alias": "data-reporting",
        "agent": {
            "name": "Data Reporting Assistant",
            "description": (
                "A professional data analysis and report generation AI assistant that analyses "
                "structured data, discovers insights, and produces professional reports. Supports "
                "weekly reports, monthly reports, ad-hoc analyses, and more."
            ),
            "instructions": (
                "You are a professional data analysis AI assistant skilled at transforming raw data "
                "into valuable business insights and structured reports.\n\n"
                "Working principles:\n"
                "1. First understand the data structure and business context before analysing\n"
                "2. Use a data-driven approach — all conclusions must be supported by evidence\n"
                "3. Reports should be clearly layered: summary -> detailed analysis -> insights -> action items\n"
                "4. Use tables, lists, and other structured formats to present comparisons and trends\n"
                "5. Flag anomalous data points for deeper analysis and hypothesise possible causes"
            ),
            "execution_mode": "auto",
        },
        "skill": {
            "name": "Data Analysis Report SOP",
            "description": (
                "A standardised data analysis report preparation process — a complete methodology "
                "from data cleaning to insight delivery."
            ),
            "content": (
                "# Data Analysis Report Standard Operating Procedure\n\n"
                "## 1. Data Understanding\n"
                "1. Data overview: confirm field definitions, data types, volume, and time range of the dataset\n"
                "2. Business context: understand the business scenario and the purpose of the analysis\n"
                "3. Quality assessment: check for missing values, outliers, and duplicate records\n\n"
                "## 2. Data Cleaning and Preprocessing\n"
                "1. Missing values: document the missing ratio and state the handling strategy (delete / impute / flag)\n"
                "2. Outliers: identify outliers using statistical methods (e.g., IQR, 3-sigma) and decide whether to retain them\n"
                "3. Data transformation: unify formats, convert units, and encode categories as needed\n\n"
                "## 3. Analysis Methods\n"
                "Select appropriate methods based on the analysis objective:\n"
                "1. **Descriptive analysis**: compute core metric means, medians, standard deviations, and distribution characteristics\n"
                "2. **Comparative analysis**: period-over-period (MoM / WoW) and year-over-year (YoY) trends and magnitudes of change\n"
                "3. **Composition analysis**: proportions by dimension and structural shifts\n"
                "4. **Correlation analysis**: relationships among key metrics\n"
                "5. **Ranking analysis**: Top N / Bottom N rankings and pattern identification\n\n"
                "## 4. Insight Extraction\n"
                "1. Extract 3-5 core findings from the data\n"
                "2. Each finding requires: supporting data + business interpretation + possible causal analysis\n"
                "3. Distinguish definitive conclusions from exploratory hypotheses\n\n"
                "## 5. Report Structure\n"
                "Output the report with the following structure:\n"
                "1. **Executive Summary**: a single paragraph summarising the core conclusions (50-100 words)\n"
                "2. **Key Metrics Dashboard**: a table of core KPIs and their changes\n"
                "3. **Detailed Analysis**: analysis expanded by dimension in separate sections\n"
                "4. **Anomaly Alerts**: flagged data points requiring attention\n"
                "5. **Action Recommendations**: specific improvement suggestions based on the analysis\n"
                "6. **Data Notes**: data sources, definitions, and limitations"
            ),
        },
    },
    # 4. IT Operations Assistant
    {
        "alias": "it-operations",
        "agent": {
            "name": "IT Operations Assistant",
            "description": (
                "An intelligent IT operations support assistant that diagnoses common technical issues, "
                "provides solution guidance, and assists with troubleshooting across networking, systems, "
                "software, and account/permission scenarios."
            ),
            "instructions": (
                "You are a professional IT operations support AI assistant responsible for helping users "
                "resolve day-to-day IT technical issues.\n\n"
                "Working principles:\n"
                "1. First ask clarifying questions about symptoms, environment, and actions already attempted\n"
                "2. Provide step-by-step solutions — each step should be clear and understandable for non-technical users\n"
                "3. Recommend the simplest, lowest-risk solution first\n"
                "4. When data operations are involved, remind the user to back up first\n"
                "5. If the issue exceeds self-service scope, clearly state that escalation is needed and suggest what information to provide to the operations team"
            ),
            "execution_mode": "auto",
        },
        "skill": {
            "name": "IT Troubleshooting SOP",
            "description": (
                "A standardised IT troubleshooting process covering high-frequency issue categories "
                "including networking, systems, software, and account/permission problems."
            ),
            "content": (
                "# IT Troubleshooting Standard Operating Procedure\n\n"
                "## 1. Information Gathering\n"
                "Before troubleshooting, confirm the following:\n"
                "1. Problem description: what exactly is the symptom? When did it start?\n"
                "2. Scope of impact: is it affecting one person or many? A specific device or all devices?\n"
                "3. Environment info: OS version, browser version, network type (LAN / WAN / VPN)\n"
                "4. Actions already taken: what troubleshooting or remediation has the user already attempted?\n"
                "5. Error messages: are there any error prompts (screenshots or text)?\n\n"
                "## 2. Network Issue Troubleshooting\n"
                "1. Basic connectivity: check cable connections / Wi-Fi status; try restarting the router/switch\n"
                "2. IP configuration: confirm IP address acquisition method (DHCP / static); check for IP conflicts\n"
                "3. DNS resolution: try pinging both domain names and IP addresses to determine whether the issue is DNS or network\n"
                "4. Proxy settings: check system/browser proxy configuration\n"
                "5. Firewall: confirm whether firewall rules are blocking access\n\n"
                "## 3. System Issue Troubleshooting\n"
                "1. Performance: check CPU / memory / disk utilisation to identify resource bottlenecks\n"
                "2. System updates: confirm OS patches are current; determine whether a recent update caused compatibility issues\n"
                "3. Driver issues: check Device Manager for abnormal devices; try updating or rolling back drivers\n"
                "4. Boot issues: test in Safe Mode to rule out third-party software interference\n\n"
                "## 4. Software Issue Troubleshooting\n"
                "1. Version check: verify the software version matches the company's required standard version\n"
                "2. Cache clearing: clear application cache and temporary files\n"
                "3. Reinstallation: uninstall and reinstall the software (remember to preserve user data)\n"
                "4. Compatibility: check the software's OS compatibility requirements\n"
                "5. Log analysis: review application logs for error messages\n\n"
                "## 5. Account and Permission Issues\n"
                "1. Account status: confirm whether the account is locked or disabled\n"
                "2. Password reset: guide the user through self-service or admin-assisted password reset\n"
                "3. Permission check: verify the user's assigned permission groups and access settings\n"
                "4. SSO / MFA: troubleshoot single sign-on or multi-factor authentication issues\n\n"
                "## 6. Escalation\n"
                "If the above steps cannot resolve the issue, provide the following to the operations team:\n"
                "- Complete problem description and reproduction steps\n"
                "- Steps already investigated and their results\n"
                "- Relevant logs and screenshots\n"
                "- Assessment of impact scope and urgency level"
            ),
        },
    },
    # 5. HR Onboarding Assistant
    {
        "alias": "hr-onboarding",
        "agent": {
            "name": "HR Onboarding Assistant",
            "description": (
                "An intelligent HR onboarding assistant that helps new employees understand the "
                "onboarding process, company policies, and benefits, answers common HR questions, "
                "and improves the onboarding experience and HR team efficiency."
            ),
            "instructions": (
                "You are a professional HR onboarding AI assistant responsible for helping new employees "
                "complete the onboarding process smoothly and integrate into the company quickly.\n\n"
                "Working principles:\n"
                "1. Be friendly and patient with every new employee's questions\n"
                "2. Answers should be accurate and specific — cite specific policy provisions when relevant\n"
                "3. Proactively share important deadlines and considerations for each onboarding stage\n"
                "4. For questions involving personal privacy (e.g., salary details), direct the employee to their HR representative\n"
                "5. Use checklists and step-by-step formats to help new employees track their onboarding progress"
            ),
            "execution_mode": "auto",
        },
        "skill": {
            "name": "New Employee Onboarding SOP",
            "description": (
                "A standardised new-employee onboarding guide covering the entire process "
                "from offer acceptance through probation completion."
            ),
            "content": (
                "# New Employee Onboarding Standard Operating Procedure\n\n"
                "## 1. Pre-Boarding (After Offer Acceptance, Before Start Date)\n"
                "1. **Document preparation checklist**:\n"
                "   - Government-issued photo ID (original + 2 copies)\n"
                "   - Academic certificates and diplomas (originals + copies)\n"
                "   - Employment separation certificate from previous employer\n"
                "   - Recent passport-size photos (4 copies)\n"
                "   - Bank account details for payroll (bank name and account number)\n"
                "   - Pre-employment medical check-up report\n"
                "2. **Information registration**: complete and submit the employee information form and emergency contact details\n"
                "3. **Pre-arrival notice**: confirm start date, reporting time and location, and dress code\n\n"
                "## 2. First Day\n"
                "1. **Check-in**: report to the HR department, submit onboarding documents\n"
                "2. **Contract signing**: sign the employment contract, NDA, and IP assignment agreement\n"
                "3. **Account setup**: receive employee badge; set up corporate email, HR portal, VPN, and other system accounts\n"
                "4. **Equipment pickup**: collect laptop and peripherals from the IT department\n"
                "5. **Workspace arrangement**: be guided to your workstation and shown nearby facilities\n"
                "6. **Team introduction**: your direct manager introduces the team and collaboration practices\n\n"
                "## 3. First Week\n"
                "1. **Company culture orientation**: learn about the company's mission, vision, core values, and code of conduct\n"
                "2. **Policy review**: read and acknowledge the employee handbook; learn about attendance, leave, and expense reimbursement policies\n"
                "3. **Role training**: attend department-level training to understand job responsibilities and goals\n"
                "4. **Buddy assignment**: connect with your onboarding buddy and set a first-month learning plan\n\n"
                "## 4. Frequently Asked Questions\n"
                "1. **Attendance policy**: standard working hours, clock-in rules, flexible work arrangements\n"
                "2. **Leave policy**: annual leave entitlement, sick leave procedures, personal leave requests\n"
                "3. **Benefits**: statutory insurance contributions, supplemental insurance, meal allowance, transport subsidy\n"
                "4. **Expense reimbursement**: approval workflow, eligible amounts, and processing timelines\n"
                "5. **Learning and development**: internal training resources, learning platforms, career progression paths\n\n"
                "## 5. Probation Period Management\n"
                "1. Probation duration: typically 3-6 months (as specified in the employment contract)\n"
                "2. Monthly check-ins: a feedback session with your direct manager once a month\n"
                "3. Confirmation review: submit a confirmation application and self-assessment 2 weeks before probation ends\n"
                "4. Confirmation process: self-assessment -> manager evaluation -> HR approval -> official confirmation notice"
            ),
        },
    },
    # 6. Sales Assistant
    {
        "alias": "sales",
        "agent": {
            "name": "Sales Assistant",
            "description": (
                "An intelligent sales support assistant that helps sales teams analyse customer needs, "
                "draft proposals, prepare quotation documents, and provides strategic advice and "
                "customer communication scripts."
            ),
            "instructions": (
                "You are an experienced sales support AI assistant helping sales teams improve "
                "efficiency and close rates.\n\n"
                "Working principles:\n"
                "1. Focus on customer needs — help salespeople deeply understand pain points and decision factors\n"
                "2. Proposals and scripts should be professional and persuasive, highlighting differentiated value\n"
                "3. Remain objective when analysing the competitive landscape — acknowledge both strengths and weaknesses\n"
                "4. Output documents should be well-formatted, use professional language, and be ready for customer delivery\n"
                "5. Provide stage-specific support based on the sales phase (lead / discovery / proposal / negotiation / close)"
            ),
            "execution_mode": "auto",
        },
        "skill": {
            "name": "Sales Proposal Writing SOP",
            "description": (
                "A standardised sales proposal creation process — a complete methodology from "
                "customer needs analysis to quotation document delivery."
            ),
            "content": (
                "# Sales Proposal Writing Standard Operating Procedure\n\n"
                "## 1. Customer Needs Analysis\n"
                "1. **Basic information gathering**: company name, industry, size, decision-maker and their role\n"
                "2. **Pain point discovery**: what core problems are they facing? What business impact do those problems cause?\n"
                "3. **Needs prioritisation**: rank customer requirements by urgency and importance\n"
                "4. **Budget range**: understand the customer's budget range and procurement cycle\n"
                "5. **Competitive analysis**: is the customer evaluating competitors? Strengths and weaknesses of competing proposals\n\n"
                "## 2. Proposal Framework Design\n"
                "1. **Value proposition**: a single sentence explaining how our solution addresses the customer's core pain point\n"
                "2. **Solution overview**: a concise description of the overall solution\n"
                "3. **Feature mapping**: map product capabilities to customer requirements one by one\n"
                "4. **Implementation plan**: a phased timeline with milestones\n"
                "5. **Return on investment**: quantified ROI analysis and expected benefits\n\n"
                "## 3. Proposal Document Writing\n"
                "Standard proposal document structure:\n"
                "1. **Cover page**: project name, customer name, date, version number\n"
                "2. **Executive summary**: a one-page overview of core value and recommended approach\n"
                "3. **Current state analysis**: outline the customer's current processes and pain points\n"
                "4. **Detailed solution**: solution architecture, feature modules, and technical advantages\n"
                "5. **Case studies**: 2-3 success stories from similar industries or scenarios\n"
                "6. **Implementation plan**: project timeline, team composition, and deliverables checklist\n"
                "7. **Investment summary**: itemised pricing, payment terms, and discount offers\n"
                "8. **Service commitment**: post-sale support, SLA commitments, and training plan\n\n"
                "## 4. Pricing Strategy\n"
                "1. Pricing principle: value-based pricing, not cost-plus\n"
                "2. Package tiers: offer 2-3 tiers (Basic / Standard / Premium)\n"
                "3. Discount policy: define discount approval authority and floor prices\n"
                "4. Payment terms: flexible options such as instalments, prepayment, or milestone-based payments\n\n"
                "## 5. Follow-Up Strategy\n"
                "1. Follow up by phone within 2 business days of sending the proposal\n"
                "2. Prepare objection-handling scripts for common concerns\n"
                "3. Record customer feedback and adjust the proposal promptly\n"
                "4. Define next steps and timeline for each interaction"
            ),
        },
    },
    # 7. Content Creation Assistant
    {
        "alias": "content-creation",
        "agent": {
            "name": "Content Creation Assistant",
            "description": (
                "A professional content creation AI assistant skilled at writing marketing copy, "
                "blog articles, product descriptions, press releases, and other content formats to "
                "help organisations improve content marketing efficiency and quality."
            ),
            "instructions": (
                "You are a professional content creation AI assistant skilled at transforming business "
                "information into high-quality content that engages the target audience.\n\n"
                "Working principles:\n"
                "1. Before writing, clarify the target audience, content purpose, and distribution channel\n"
                "2. Content should have a clear structure, strong perspectives, and smooth narrative flow\n"
                "3. Headlines should be compelling — able to capture attention within 3 seconds\n"
                "4. Use data, case studies, and storytelling to strengthen persuasiveness\n"
                "5. Adapt tone and format to the platform (blog, LinkedIn, newsletter, social media, etc.)"
            ),
            "execution_mode": "auto",
        },
        "skill": {
            "name": "Marketing Content Creation SOP",
            "description": (
                "A standardised marketing content creation process covering the full methodology "
                "from topic planning to publication optimisation."
            ),
            "content": (
                "# Marketing Content Creation Standard Operating Procedure\n\n"
                "## 1. Pre-Writing Preparation\n"
                "1. **Goal setting**:\n"
                "   - Content purpose: brand awareness / lead generation / user education / sales conversion\n"
                "   - Target audience: persona (industry, role, pain points, interests)\n"
                "   - Distribution channel: blog / LinkedIn / company website / newsletter / social media\n"
                "   - KPI targets: views / shares / engagement rate / conversion rate\n\n"
                "2. **Topic planning**:\n"
                "   - Trending topics: tie in with industry news, policy changes, or seasonal events\n"
                "   - Pain-point topics: address the target audience's core challenges and common questions\n"
                "   - Thought-leadership topics: industry trends, technology shifts, market forecasts\n"
                "   - Case-study topics: customer success stories and best-practice showcases\n\n"
                "## 2. Content Structure Design\n"
                "General article structure (adjust for specific content type):\n"
                "1. **Headline**: compelling, includes core keywords, under 70 characters\n"
                "2. **Lead**: the first 100 words must hook the reader by posing a problem or promising value\n"
                "3. **Body**: develop 3-5 core points, each supported by evidence\n"
                "4. **Proof points**: data, case studies, or testimonials that build credibility\n"
                "5. **Summary / CTA**: recap key takeaways and guide the reader to take action\n\n"
                "## 3. Writing Standards\n"
                "1. **Tone**: adjust for channel (blog is conversational, website copy is more formal)\n"
                "2. **Paragraph length**: keep paragraphs under 4 lines on mobile (~100 words)\n"
                "3. **Formatting**: use subheadings, bold text, and lists to improve readability\n"
                "4. **SEO optimisation**: weave in core keywords naturally — do not keyword-stuff\n"
                "5. **Compliance check**: avoid superlative claims unless substantiated (best, only, first, etc.)\n\n"
                "## 4. Review Process\n"
                "1. Fact-checking: are data points and case study sources reliable?\n"
                "2. Logic check: are arguments fully supported and reasoning sound?\n"
                "3. Proofreading: spelling, punctuation, and formatting consistency\n"
                "4. Brand consistency: does the tone and terminology match brand guidelines?\n"
                "5. Compliance review: any sensitive topics or non-compliant language?\n\n"
                "## 5. Publication Optimisation\n"
                "1. Publish timing: schedule for when the target audience is most active\n"
                "2. Featured image: design a cover image that aligns with the headline\n"
                "3. Meta description: write an engaging summary/description\n"
                "4. Engagement prompts: add discussion starters to encourage reader interaction"
            ),
        },
    },
    # 8. Meeting Summary Assistant
    {
        "alias": "meeting-summary",
        "agent": {
            "name": "Meeting Summary Assistant",
            "description": (
                "An intelligent meeting summary AI assistant that organises meeting notes and "
                "transcripts, extracts key decisions and action items, and generates structured "
                "meeting minutes. Supports recurring meetings and ad-hoc sessions."
            ),
            "instructions": (
                "You are a professional meeting summary AI assistant skilled at transforming "
                "unstructured meeting notes into clear, well-organised meeting minutes.\n\n"
                "Working principles:\n"
                "1. Accurately extract key information from the meeting — do not add content not in the source\n"
                "2. Distinguish between decisions, discussion items, and follow-up tasks\n"
                "3. Every action item must have a clear owner and due date\n"
                "4. Maintain an objective record — do not inject personal opinions or bias\n"
                "5. Use concise, clear language and avoid redundancy"
            ),
            "execution_mode": "auto",
        },
        "skill": {
            "name": "Meeting Minutes SOP",
            "description": (
                "A standardised meeting minutes preparation process — a complete methodology "
                "from raw notes to structured output."
            ),
            "content": (
                "# Meeting Minutes Standard Operating Procedure\n\n"
                "## 1. Information Gathering\n"
                "Before organising, confirm the following basic information:\n"
                "1. **Meeting title**: the official name or topic of the meeting\n"
                "2. **Date and time**: start and end times\n"
                "3. **Location**: physical location or virtual meeting link\n"
                "4. **Attendees**: list of participants, observers, and absentees\n"
                "5. **Chairperson**: the meeting facilitator\n"
                "6. **Note-taker**: the person recording the meeting\n\n"
                "## 2. Content Categorisation\n"
                "Classify meeting content into the following categories:\n\n"
                "### 2.1 Agenda Review\n"
                "Record discussion content item by item following the agenda:\n"
                "- Topic title\n"
                "- Presenter / initiator\n"
                "- Summary of key discussion points (retain essential arguments, remove repetition and tangents)\n"
                "- Different viewpoints and areas of disagreement\n\n"
                "### 2.2 Decisions\n"
                "Clearly record all decisions made during the meeting:\n"
                "- Description of the decision\n"
                "- Rationale or context\n"
                "- Whether it was unanimous / vote outcome\n\n"
                "### 2.3 Action Items\n"
                "Each action item must include:\n"
                "- **Task description**: the specific work to be completed\n"
                "- **Owner**: assigned to a named individual (not a department)\n"
                "- **Due date**: a specific date (not vague terms like \"ASAP\")\n"
                "- **Deliverable**: the expected output or artefact\n"
                "- **Collaborators**: other people who need to contribute\n\n"
                "### 2.4 Open Issues\n"
                "Record issues not resolved in this meeting:\n"
                "- Problem description\n"
                "- Planned resolution path (next meeting / offline discussion / dedicated review)\n"
                "- Follow-up owner\n\n"
                "## 3. Minutes Format\n"
                "Standard output format:\n"
                "```\n"
                "# Meeting Minutes\n"
                "Meeting Title: XXX\n"
                "Date & Time: YYYY-MM-DD HH:MM - HH:MM\n"
                "Location: XXX\n"
                "Chairperson: XXX\n"
                "Note-taker: XXX\n"
                "Attendees: XXX, XXX, XXX\n\n"
                "## 1. Agenda Discussion\n"
                "### 1.1 [Topic Title]\n"
                "...\n\n"
                "## 2. Decisions\n"
                "1. [Decision 1]...\n\n"
                "## 3. Action Items\n"
                "| # | Task | Owner | Due Date | Notes |\n"
                "| --- | --- | --- | --- | --- |\n\n"
                "## 4. Open Issues\n"
                "1. [Issue 1]...\n\n"
                "## 5. Next Meeting\n"
                "Date: XXX  Topics: XXX\n"
                "```\n\n"
                "## 4. Quality Check\n"
                "1. Confirm all action items have a clear owner and due date\n"
                "2. Confirm decisions are recorded completely and without ambiguity\n"
                "3. Confirm the attendee list matches actual attendance\n"
                "4. Confirm the meeting minutes are distributed within 24 hours of the meeting"
            ),
        },
    },
]


# ---------------------------------------------------------------------------
# Import function (upsert-by-name)
# ---------------------------------------------------------------------------


class ImportResult(TypedDict):
    created: int
    updated: int
    skipped: int


async def import_solution_templates(
    db: AsyncSession,
    market_org_id: str,
    owner_id: str,
) -> ImportResult:
    """Import solution templates into the Market org using upsert-by-name.

    For each template:
    - If an Agent with the same name already exists in the Market org,
      update the agent's description, instructions, and execution_mode,
      then find and update the linked skill's description, content, and
      resource_refs.
    - If no matching Agent exists, create a new Agent first, then create
      a Skill with ``resource_refs`` pointing back to the Agent.

    Binding direction: Skill -> Agent via ``Skill.resource_refs``.
    ``Agent.skill_ids`` is NOT set by this function (deprecated).

    The function flushes but does **not** commit — the caller is responsible
    for committing the transaction.

    Parameters
    ----------
    db:
        An active async database session.
    market_org_id:
        The Market organisation ID.
    owner_id:
        The user ID to assign as owner for newly created records.

    Returns
    -------
    ImportResult
        Counts of created, updated, and skipped templates.
    """
    created = 0
    updated = 0

    for template in SOLUTION_TEMPLATES:
        agent_cfg = template["agent"]
        skill_cfg = template["skill"]
        alias = template["alias"]

        # Check if agent with this name already exists in Market org
        result = await db.execute(
            select(Agent).where(
                Agent.name == agent_cfg["name"],
                Agent.org_id == market_org_id,
            )
        )
        existing_agent = result.scalar_one_or_none()

        if existing_agent is not None:
            # UPDATE existing agent
            existing_agent.description = agent_cfg["description"]
            existing_agent.instructions = agent_cfg["instructions"]
            existing_agent.execution_mode = agent_cfg["execution_mode"]

            # Find linked skill by name in Market org
            existing_skill_result = await db.execute(
                select(Skill).where(
                    Skill.name == skill_cfg["name"],
                    Skill.org_id == market_org_id,
                )
            )
            existing_skill = existing_skill_result.scalar_one_or_none()

            resource_ref = {
                "type": "agent",
                "id": existing_agent.id,
                "name": existing_agent.name,
                "alias": f"@{alias}",
            }

            if existing_skill is not None:
                existing_skill.description = skill_cfg["description"]
                existing_skill.content = skill_cfg["content"]
                existing_skill.resource_refs = [resource_ref]
            else:
                # Skill doesn't exist yet, create it
                now = datetime.now(timezone.utc)
                skill = Skill(
                    name=skill_cfg["name"],
                    description=skill_cfg["description"],
                    content=skill_cfg["content"],
                    user_id=owner_id,
                    visibility="org",
                    org_id=market_org_id,
                    is_active=True,
                    status="published",
                    publish_status="approved",
                    published_at=now,
                    resource_refs=[resource_ref],
                )
                db.add(skill)

            updated += 1
        else:
            # CREATE new Agent first, then Skill with resource_refs
            now = datetime.now(timezone.utc)
            agent = Agent(
                name=agent_cfg["name"],
                description=agent_cfg["description"],
                instructions=agent_cfg["instructions"],
                execution_mode=agent_cfg["execution_mode"],
                user_id=owner_id,
                visibility="org",
                org_id=market_org_id,
                is_active=True,
                status="published",
                publish_status="approved",
                published_at=now,
            )
            db.add(agent)
            await db.flush()  # generate agent.id

            skill = Skill(
                name=skill_cfg["name"],
                description=skill_cfg["description"],
                content=skill_cfg["content"],
                user_id=owner_id,
                visibility="org",
                org_id=market_org_id,
                is_active=True,
                status="published",
                publish_status="approved",
                published_at=now,
                resource_refs=[
                    {
                        "type": "agent",
                        "id": agent.id,
                        "name": agent.name,
                        "alias": f"@{alias}",
                    }
                ],
            )
            db.add(skill)
            created += 1

    await db.flush()

    if created > 0 or updated > 0:
        logger.info(
            "Solution template import: created=%d, updated=%d (Market org %s)",
            created,
            updated,
            market_org_id,
        )
    else:
        logger.debug("Solution templates already up to date, nothing to import")

    return ImportResult(created=created, updated=updated, skipped=0)
