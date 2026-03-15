export interface SkillResponse {
  id: string
  user_id: string | null
  source?: string
  name: string
  description: string | null
  content: string
  script: string | null
  script_type: "python" | "shell" | null
  visibility: string
  org_id: string | null
  is_active: boolean
  status: string
  publish_status: string | null
  published_at: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  review_note: string | null
  created_at: string
  updated_at: string | null
}

export interface SkillCreate {
  name: string
  description?: string | null
  content: string
  script?: string | null
  script_type?: "python" | "shell" | null
  is_active?: boolean
}

export interface SkillUpdate {
  name?: string
  description?: string | null
  content?: string
  script?: string | null
  script_type?: "python" | "shell" | null
  is_active?: boolean
}
