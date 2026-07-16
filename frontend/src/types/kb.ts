export interface KBResponse {
  id: string
  user_id: string
  name: string
  description: string | null
  chunk_strategy: string
  chunk_size: number
  chunk_overlap: number
  retrieval_mode: string
  document_count: number
  total_chunks: number
  status: string
  is_active: boolean
  visibility?: string // always "personal" — KBs are not shareable
  org_id?: string | null
  publish_status: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  review_note: string | null
  created_at: string
  updated_at: string | null
}

export interface KBDocumentResponse {
  id: string
  kb_id: string
  filename: string
  file_size: number
  file_type: string
  chunk_count: number
  status: string
  error_message: string | null
  created_at: string
}

export interface KBCreate {
  name: string
  description?: string | null
  chunk_strategy?: string
  chunk_size?: number
  chunk_overlap?: number
  retrieval_mode?: string
}

export interface KBUpdate {
  name?: string
  description?: string | null
  chunk_strategy?: string
  chunk_size?: number
  chunk_overlap?: number
  retrieval_mode?: string
}

export interface KBRetrieveResult {
  content: string
  metadata: Record<string, unknown>
  score: number
}

export interface ChunkResponse {
  id: string
  text: string
  chunk_index: number
  metadata: Record<string, unknown> | null
  content_hash: string
}

export interface PaginatedChunks {
  items: ChunkResponse[]
  total: number
  page: number
  size: number
  pages: number
}

export interface PaginatedDocuments {
  items: KBDocumentResponse[]
  total: number
  page: number
  size: number
  pages: number
}

export interface ChunkUpdate {
  text: string
}

export interface DocumentCreate {
  filename: string
  content: string
}
