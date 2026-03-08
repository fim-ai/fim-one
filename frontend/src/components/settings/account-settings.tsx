"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useTranslations, useLocale } from "next-intl"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { InputOTP, InputOTPGroup, InputOTPSlot, InputOTPSeparator } from "@/components/ui/input-otp"
import { apiFetch, authApi, ApiError } from "@/lib/api"
import { toast } from "sonner"
import { useAuth } from "@/contexts/auth-context"
import { ACCESS_TOKEN_KEY, getApiDirectUrl } from "@/lib/constants"
import type { UserInfo } from "@/types/auth"

const MIN_PASSWORD_LENGTH = 8
const SUPPORTED_PROVIDERS = ["github", "google", "discord", "feishu"] as const

function ProviderIcon({ provider }: { provider: string }) {
  if (provider === "github") {
    return (
      <svg className="h-5 w-5 shrink-0" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
      </svg>
    )
  }
  if (provider === "google") {
    return (
      <svg className="h-5 w-5 shrink-0" viewBox="0 0 24 24">
        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
      </svg>
    )
  }
  if (provider === "discord") {
    return (
      <svg className="h-5 w-5 shrink-0" viewBox="0 0 24 24" fill="currentColor">
        <path d="M20.317 4.37a19.791 19.791 0 00-4.885-1.515.074.074 0 00-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 00-5.487 0 12.64 12.64 0 00-.617-1.25.077.077 0 00-.079-.037A19.736 19.736 0 003.677 4.37a.07.07 0 00-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 00.031.057 19.9 19.9 0 005.993 3.03.078.078 0 00.084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 00-.041-.106 13.107 13.107 0 01-1.872-.892.077.077 0 01-.008-.128 10.2 10.2 0 00.372-.292.074.074 0 01.077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 01.078.01c.12.098.246.198.373.292a.077.077 0 01-.006.127 12.299 12.299 0 01-1.873.892.077.077 0 00-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 00.084.028 19.839 19.839 0 006.002-3.03.077.077 0 00.032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 00-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" />
      </svg>
    )
  }
  if (provider === "feishu") {
    return (
      <svg className="h-5 w-5 shrink-0" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
        <path d="M770.91584 373.312c-2.688 0.128-3.392-1.088-3.648-3.648-0.32-2.688-0.128-5.952-1.472-8.064-2.56-4.032-1.856-8.768-4.032-12.928a23.04 23.04 0 0 1-2.56-7.04c-0.128-1.536 0.256-3.584-0.832-4.352-2.304-1.664-1.408-4.416-2.88-6.592-1.6-2.368-1.728-5.76-2.944-8.64-1.28-3.008-2.752-6.208-3.072-9.536-0.128-1.6-1.92-1.664-1.92-3.264 0.192-2.688-1.792-4.928-2.432-7.488-0.896-3.392-2.816-6.72-4.096-10.048a78.528 78.528 0 0 0-3.392-7.936c-1.856-3.648-3.392-7.36-5.504-10.944-2.048-3.2-2.56-7.232-4.8-10.688a59.2 59.2 0 0 1-4.672-8.96c-2.112-4.992-5.248-9.344-7.68-14.08a196.48 196.48 0 0 0-7.488-13.824c-2.56-4.288-4.8-8.832-7.808-12.992-1.216-1.728-3.072-3.392-3.392-5.312-0.448-2.752-2.56-4.736-3.84-6.4-1.856-2.112-1.92-6.016-5.504-6.848 0 0-0.128-0.32-0.064-0.512 0.256-2.88-3.84-3.712-3.456-6.72-3.2-1.088-2.24-5.696-5.824-6.592 0 0-0.192-0.256-0.128-0.384 0.32-2.944-2.56-4.544-3.776-6.464a48.96 48.96 0 0 0-5.504-6.848c-0.96-1.024-2.56-1.92-2.88-3.2-1.28-4.352-5.44-6.528-7.552-10.368-1.984-3.52-5.44-5.824-8.128-8.768a28.16 28.16 0 0 0-9.536-7.296c-3.392-1.28-6.144-4.096-10.24-4.352-4.224-0.192-8.256-2.688-12.672-2.56C617.18784 128.704 616.86784 128 615.71584 128H172.38784c-1.216 0-1.472 0.704-1.472 1.664-2.944-0.192-4.544 2.24-5.504 3.968-2.176 3.84 0.96 10.944 5.184 12.672 1.792 0.64 2.752 1.92 3.968 3.072 0.64 0.64 1.28 1.664 2.176 1.536 1.792-0.192 2.56 1.088 3.072 2.112 0.64 1.408 1.92 1.92 2.944 2.432a37.12 37.12 0 0 1 8.512 6.016c3.776 3.52 8 6.4 12.16 9.408 3.392 2.496 6.144 5.76 9.92 7.808 2.816 1.472 4.352 4.736 7.04 6.08 3.84 1.92 5.952 5.568 9.6 7.424a19.328 19.328 0 0 1 5.632 4.992c1.472 1.664 4.096 1.792 5.312 3.456 3.392 5.12 9.28 7.424 13.312 12.032 3.904 4.48 9.472 7.168 13.568 11.904 4.864 5.632 11.392 9.6 16.64 14.912 3.712 3.648 7.424 7.04 11.136 10.56 2.944 2.752 5.888 5.44 8.64 8.32 2.048 2.048 4.672 3.648 6.592 5.44 4.736 4.608 10.24 8.704 13.568 14.656l0.704 0.896c3.648 3.392 7.296 6.848 10.88 10.368 4.352 4.352 8.576 9.088 13.312 13.248a17.92 17.92 0 0 1 4.864 5.376c0.96 2.24 2.56 3.648 4.096 5.12 4.032 3.84 8.384 7.552 11.264 12.544 1.152 2.048 3.584 2.752 4.8 4.736 1.6 2.496 4.032 4.224 5.632 6.912 2.368 3.84 6.4 6.528 9.088 10.56 2.624 3.968 6.144 7.744 9.536 11.136 3.776 3.84 6.4 8.448 10.432 11.904 2.112 1.792 2.304 5.12 5.056 6.208 1.344 0.64 1.792 1.792 2.176 2.752 1.472 3.136 4.16 5.312 5.888 8.256 1.664 2.88 4.48 4.736 6.4 7.616 3.52 5.376 7.552 10.56 11.904 15.36 1.408 1.6 1.28 4.096 3.328 4.864 3.072 1.28 3.584 4.544 5.12 6.72 2.112 2.88 4.864 5.312 6.4 8.448 1.664 3.328 4.416 5.824 6.144 8.96 1.664 2.752 3.712 5.248 5.376 8 1.28 2.112 2.432 4.48 4.224 6.4 1.92 1.92 2.56 4.928 4.864 6.784 2.048 1.664 2.432 4.352 4.352 6.272 1.984 2.176 2.816 5.44 4.992 7.552 1.92 1.792 2.816 4.288 4.16 6.4 0.96 1.472 2.944 2.368 3.2 4.352 0.192 2.176 1.856 3.52 2.944 5.12 1.664 2.496 3.84 4.928 4.8 7.296 1.792 4.416 4.8 7.744 6.912 11.712 2.496 4.352 5.568 8.768 8.064 13.312 1.92 3.264 3.776 6.592 5.824 9.6 0.896 1.6 0.704 3.648 2.432 4.8 2.048 1.472 2.496 3.968 3.648 6.016 1.024 1.792 1.728 4.032 3.2 5.248 1.152 1.024 1.152 1.92 1.408 3.2 1.728 0.64 2.624-0.704 3.648-1.664l8.192-8.128 16.896-16.64c4.8-4.8 9.28-9.856 14.272-14.464 8.768-8 16.832-16.64 25.6-24.768 5.696-5.312 10.816-11.2 17.088-16 5.504-4.16 10.112-9.472 15.104-14.336l11.008-11.008c3.84-3.84 8.768-6.272 12.288-10.368a3.072 3.072 0 0 1 1.088-0.96c3.712-1.472 6.272-4.48 9.344-6.72 3.584-2.56 7.168-5.12 10.624-7.808 1.792-1.408 4.032-2.048 5.568-3.584 4.416-4.416 10.24-6.4 15.36-9.472 5.44-3.2 11.008-6.4 16.64-9.088 4.096-1.92 7.68-4.928 12.032-5.76 5.12-1.088 8.768-4.48 13.568-6.016 1.472-0.384 3.264-0.576 4.608-1.6 2.752-2.176 6.528-2.176 9.6-3.648 1.472-0.704 3.136-1.664 4.8-1.92 3.84-0.448 7.168-2.24 10.88-3.2 0.96-0.192 1.792-0.768 0.896-2.048" fill="#00D6B9"/>
        <path d="M876.19584 641.28c-1.024-0.64-1.728 0.256-2.368 0.896-1.92 1.792-3.392 4.096-5.056 6.144l-8.384 9.856c-5.76 6.336-12.032 12.416-18.304 18.112-4.928 4.48-10.368 8.32-15.68 12.288-1.92 1.472-3.904 3.072-5.952 4.416-2.24 1.536-4.16 3.584-6.592 4.608-4.608 1.92-8.704 4.8-12.992 7.296-7.424 4.096-15.168 7.68-23.04 10.752-4.48 1.856-8.896 3.84-13.44 5.248-3.712 1.152-7.296 2.752-11.008 3.648-1.28 0.32-2.56 0.192-3.776 0.64-3.648 1.344-7.488 2.176-11.328 3.136-3.264 0.832-6.848 0.128-9.728 1.472-4.352 2.048-9.344 0.128-13.44 2.88-6.4 0-12.736 0.64-19.072 1.216-11.264 0.832-22.528-0.768-33.792-1.088-5.12-0.128-9.984-2.688-15.168-1.92a1.088 1.088 0 0 1-0.832-0.32c-1.664-1.28-3.648-1.28-5.504-1.28-3.968 0-7.808-0.832-11.52-1.536-3.648-0.768-7.36-2.176-10.944-3.392-2.112-0.704-4.416-0.32-6.336-1.28a32.576 32.576 0 0 0-8.768-2.752c-7.04-1.472-13.504-4.416-20.608-5.568-4.672-0.768-8.768-3.392-13.44-4.352-3.328-0.768-6.528-1.92-9.664-2.944-4.032-1.28-8.064-2.688-12.16-3.712-6.528-1.792-12.48-4.544-18.88-6.592-7.616-2.56-14.976-5.504-22.592-8.064-4.48-1.472-8.448-4.16-13.312-4.928a16.768 16.768 0 0 1-5.824-2.176c-3.584-2.048-7.68-2.752-11.264-4.48-3.072-1.536-6.528-2.24-9.536-3.712-4.992-2.56-10.752-3.328-15.296-6.912a1.92 1.92 0 0 0-1.088-0.32c-3.648-0.384-6.912-2.048-10.112-3.648-6.912-3.392-14.4-5.632-21.184-9.344-5.44-3.008-11.392-4.736-16.832-7.68-1.28-0.256-2.368 0-3.648-0.64-2.24-1.152-4.416-2.944-6.72-3.584-4.48-1.216-7.808-4.48-12.224-5.76-2.496-0.704-4.416-2.944-6.72-3.52-2.944-0.704-5.376-2.176-7.68-3.52-3.84-2.176-8.064-3.648-11.968-6.016-2.112-1.344-4.864-1.792-6.72-3.328-2.752-2.304-7.04-2.176-8.96-5.696-4.672-0.384-7.808-3.968-11.84-5.632-4.8-1.92-9.088-4.992-13.632-7.552-1.92-1.088-3.648-3.072-5.504-3.392-4.928-1.024-8.32-4.544-12.416-6.784a142.72 142.72 0 0 1-13.888-8.32c-0.768-0.448-1.856-0.384-2.368-0.96-2.88-3.264-7.04-4.928-10.688-7.168a484.608 484.608 0 0 1-10.56-6.4c-4.352-2.752-8.768-5.44-12.864-8.32-2.752-1.984-6.144-3.2-8.512-5.44-1.728-1.6-3.84-2.176-5.632-3.648-3.2-2.624-6.976-4.8-10.432-7.168-1.6-1.088-3.776-2.112-4.864-3.328-2.112-2.304-4.992-3.52-7.232-5.376-3.392-2.88-7.616-4.864-10.944-7.872-1.984-1.728-4.928-2.048-6.4-4.48-0.512-0.96-1.472-1.664-2.752-1.984-2.24-0.64-3.776-2.432-5.568-3.648-2.688-1.856-5.12-4.352-7.936-6.272-3.328-2.24-6.272-5.248-9.728-7.296-3.712-2.24-6.336-5.76-10.048-7.872-2.304-1.408-3.648-3.968-5.888-4.992-3.904-1.728-6.272-5.184-9.856-7.296-2.752-1.792-4.672-5.12-7.616-6.592-3.456-1.856-5.504-5.056-8.576-7.168-0.832-0.64-1.92-0.768-2.688-1.792-1.92-2.368-4.672-4.16-6.912-6.272-2.432-2.176-5.44-3.648-7.68-6.272-1.728-1.92-3.584-4.48-5.76-5.376-3.392-1.28-4.864-4.224-7.488-6.144-3.328-2.304-5.76-5.696-8.96-8.256-2.048-1.6-4.352-2.88-5.952-4.928-2.432-3.072-5.504-5.696-8.32-8.32C63.58784 417.92 60.06784 414.208 56.35584 410.752c-2.944-2.56-5.632-5.504-8.64-8.192-2.752-2.432-5.248-5.248-8.064-7.68C35.93984 391.424 32.54784 387.584 29.09184 384 25.63584 380.416 21.92384 377.024 18.53184 373.312 15.65184 370.048 12.70784 366.08 7.07584 367.232c-2.816 0.64-4.096 2.368-5.12 4.608-2.88 0.32-1.728 2.56-1.728 3.776v414.208c0 1.664 0.192 3.264 0.128 4.864 0 1.28 0.384 1.856 1.6 1.92-0.768 3.84 1.792 6.848 2.368 10.368 0.384 2.56 1.984 4.608 2.944 6.976 1.408 3.2 4.096 5.888 6.016 8.96a24.96 24.96 0 0 0 6.4 6.72c2.688 1.92 5.376 3.904 8.192 5.632 3.392 1.92 6.592 4.288 9.728 6.464 0.96 0.64 2.048 0.768 3.072 1.728a47.36 47.36 0 0 0 10.24 6.4c2.432 1.28 4.736 2.944 7.36 4.032 5.824 2.432 10.816 6.784 16.832 9.152 3.328 1.28 5.888 3.648 9.344 4.672 0.768 0.32 1.728 0.384 2.432 0.832 4.8 3.328 10.24 5.248 15.488 7.872 1.088 0.64 2.496 0.64 3.328 1.28a21.504 21.504 0 0 0 9.536 4.288c0.64 0.128 1.152 0.192 1.856 0.704 1.408 1.088 3.2 1.92 4.928 2.88 1.92 1.088 4.416 0.448 6.144 2.304 0.64 0.768 2.24 1.728 3.456 1.92 5.12 0.96 9.856 3.392 14.656 5.248 1.408 0.64 3.328 0 4.352 1.152 1.92 1.92 4.608 1.92 6.784 2.88 3.2 1.408 6.592 2.56 10.24 3.392 1.472 0.384 3.584 0 4.544 1.088 1.856 2.048 4.608 2.176 6.592 2.688 5.248 1.28 10.368 2.944 15.488 4.672 4.096 1.472 8.704 1.792 13.056 3.072 3.648 1.024 7.296 2.24 10.944 2.88 1.728 0.32 4.16-0.384 5.12 0.512 2.816 2.752 6.592 1.152 9.728 2.688a23.552 23.552 0 0 0 11.264 1.792c0.768-0.064 1.856-0.256 2.24 0.128 2.88 2.944 7.424 0 10.24 3.008 6.528 0.576 13.12 0.896 19.584 2.752 4.16 1.28 8.768 0.64 13.184 1.6 5.312 1.28 11.072 0.896 16.64 1.472 3.648 0.32 7.232 0.192 10.88 0.256 0 1.152 0.64 1.6 1.792 1.536h78.976c1.152 0 1.856-0.384 1.856-1.536 8.768 0.448 17.408-1.6 26.112-1.28h4.8c1.216 0 1.856-0.448 1.792-1.664 3.904-0.256 7.808-0.704 11.712-1.472 6.144-1.28 12.544-0.96 18.688-2.944 3.072-1.024 6.592-0.832 9.856-1.664 3.84-0.896 7.872-1.92 12.032-1.6 0.448 0 1.152 0 1.408-0.192 2.048-2.752 5.44-1.472 8.192-2.752a21.184 21.184 0 0 1 9.472-1.664c0.448 0 1.024 0 1.472-0.192a23.488 23.488 0 0 1 9.344-3.008 62.08 62.08 0 0 0 12.992-3.392 56.96 56.96 0 0 1 9.6-2.56c0.64-0.128 1.472-0.128 1.856-0.576 1.856-1.92 4.48-1.92 6.592-2.56 4.224-1.088 8.256-2.688 12.416-3.968 1.408-0.32 3.008 0.064 4.16-0.704a25.792 25.792 0 0 1 10.88-4.416 3.392 3.392 0 0 0 1.792-0.576c1.92-1.152 3.648-2.56 6.08-2.816 3.328-0.384 6.592-2.048 9.472-3.52 2.56-1.28 5.312-2.24 7.872-3.52 1.92-1.024 4.096-2.304 6.464-2.752 3.456-0.576 5.76-3.52 9.216-4.288a25.984 25.984 0 0 0 7.488-3.2c3.712-2.048 7.616-3.84 11.392-5.632 2.432-1.28 5.184-2.304 7.168-3.712a54.336 54.336 0 0 1 9.536-5.312c4.224-1.92 8-4.608 12.16-6.4 3.712-1.728 7.04-4.416 10.624-6.4 1.856-1.088 3.52-2.688 5.312-3.584 1.984-1.024 3.968-2.176 5.824-3.392 2.496-1.728 5.44-2.752 7.552-4.864 1.92-1.92 4.48-2.56 6.4-4.288 2.368-1.984 5.504-2.944 7.68-5.12 0.768-0.768 1.216-1.472 2.24-1.472a3.52 3.52 0 0 0 2.688-1.856 6.272 6.272 0 0 1 2.944-2.496c3.456-1.216 5.248-4.48 8.448-6.144 1.984-0.896 3.648-2.752 5.504-4.096 3.2-2.176 6.144-4.48 9.152-6.848 1.664-1.28 3.136-3.584 4.8-4.096 3.712-1.152 4.8-5.12 8.192-6.592a11.328 11.328 0 0 0 3.2-2.368c2.56-2.88 5.76-5.248 8.768-8.064 1.792-1.92 4.288-2.816 6.08-4.864a52.608 52.608 0 0 1 6.848-6.912 57.984 57.984 0 0 0 6.144-5.888l10.944-10.88c3.648-3.52 7.104-7.104 10.752-10.56a45.824 45.824 0 0 0 5.888-6.848 37.056 37.056 0 0 1 6.592-6.976c1.92-1.536 2.496-3.84 4.224-5.44a31.104 31.104 0 0 0 6.656-7.552c1.92-3.392 5.312-5.504 7.168-8.96 1.28-2.432 3.84-4.096 5.376-6.528 1.6-2.624 3.712-4.992 5.696-7.36 2.176-2.56 4.672-5.376 6.144-8.32 1.536-3.2 4.288-5.12 5.76-8.32a49.536 49.536 0 0 1 5.696-8.064c1.472-1.856 2.368-3.84 3.584-5.76 1.92-2.944 3.968-5.76 6.016-8.64 0.768-1.28 0.96-2.56 2.048-3.84 1.92-2.048 4.16-4.48 3.648-7.872" fill="#3370FF"/>
        <path d="M1022.49984 392.32c-0.384-0.576-0.832-0.576-1.408-0.704-1.664-0.512-3.456-0.896-4.928-1.728a30.08 30.08 0 0 0-5.12-2.944c-2.688-1.088-5.824-1.472-8.128-3.072-3.136-2.432-7.04-2.368-10.432-4.096a26.24 26.24 0 0 0-7.68-2.752c-1.984-0.32-4.096-1.28-6.08-1.92-2.688-0.832-5.568-1.792-8.32-2.56a163.84 163.84 0 0 1-12.992-3.712 22.848 22.848 0 0 0-9.216-1.536c-0.704-2.944-3.136-1.216-4.8-1.6-2.176 0-4.224-1.088-6.272-1.408-6.656-1.216-13.44-1.408-20.224-2.944-5.312-1.152-11.136-0.512-16.64-1.28-3.328-0.512-6.592-0.192-9.984-0.256 0-1.28-0.64-1.6-1.792-1.6h-32.128c-1.152 0-1.856 0.32-1.856 1.6-8.192-0.448-16.192 1.344-24.384 1.28h-4.864c-1.152 0-1.664 0.448-1.728 1.536-4.672-0.384-9.088 1.664-13.76 1.28a1.472 1.472 0 0 0-0.832 0.256c-3.584 2.56-7.936 1.664-11.84 2.944-3.072 1.024-6.784 0.896-9.728 1.984-2.368 0.832-4.992 1.472-7.296 2.432a21.312 21.312 0 0 1-9.152 1.792c0.064 0.64 0.192 1.28-0.64 1.408-1.792 0.128-3.456 1.088-5.12 1.472-4.8 1.28-9.344 3.072-14.016 4.672-4.096 1.472-7.872 3.648-11.904 4.608-4.608 1.088-7.808 4.736-12.672 5.312a8.064 8.064 0 0 0-4.16 1.472 15.04 15.04 0 0 1-4.48 2.752 30.08 30.08 0 0 0-7.296 3.584c-3.328 2.176-7.04 3.648-10.24 5.76-1.28 0.96-3.264 0.576-3.84 1.536-2.368 4.096-7.168 4.16-10.496 6.72-3.52 2.688-7.68 4.736-11.2 7.36-2.88 2.24-5.952 4.288-8.768 6.592-3.648 3.2-7.616 6.016-11.52 8.768-1.984 1.472-3.2 4.224-5.376 4.864-3.328 1.152-5.12 3.968-7.296 6.016-2.176 2.176-4.672 4.48-6.912 6.784-3.328 3.456-6.912 6.656-10.432 9.984-2.624 2.56-4.928 5.632-7.808 7.552-4.416 2.944-7.68 6.784-11.264 10.24-3.648 3.456-7.36 7.04-10.88 10.624-4.032 4.16-8.576 7.872-12.48 12.16-3.328 3.84-7.232 7.168-10.816 10.624-3.648 3.584-7.04 7.424-10.944 10.88-3.84 3.456-7.424 7.04-10.88 10.816-2.752 2.88-5.696 5.76-8.576 8.512-2.944 2.816-5.44 6.08-9.088 8.064-0.384 1.792-1.856 2.496-3.2 3.392-3.776 2.368-6.784 5.696-10.048 8.768-2.176 1.92-3.968 4.608-6.4 6.016-4.16 2.176-7.04 5.824-10.688 8.448-4.416 3.2-8.96 6.528-12.8 10.368-1.92 1.856-4.864 2.176-6.08 4.544-1.024 2.048-3.648 1.6-4.928 3.2-2.56 3.456-6.72 4.864-9.792 7.68-1.856 1.664-4.416 3.008-6.592 4.352-1.408 0.704-2.944 1.28-4.096 2.368a47.36 47.36 0 0 1-9.408 6.528c-1.472 0.896-3.456 1.472-4.544 2.752-2.048 2.176-4.928 3.2-7.232 5.12a29.568 29.568 0 0 1-7.744 4.672c-3.712 1.408-6.464 3.968-9.856 5.632-4.48 2.176-8.768 4.864-13.248 7.04-1.152 0.64-3.136 1.024-3.776 2.56 0 1.088 0.448 1.344 1.28 1.92 2.048 1.152 4.224 1.408 6.208 2.24 3.2 1.472 6.272 3.264 9.472 4.608 3.072 1.28 5.632 3.328 9.28 3.648 1.664 0.32 3.2 1.088 4.48 2.176 2.176 1.92 4.992 2.624 7.296 3.52 2.048 0.704 4.032 2.048 6.4 2.496 1.472 0.256 3.456 0.192 4.608 1.088 2.048 1.6 4.352 2.496 6.592 3.84 1.472 0.832 3.392-0.32 4.288 0.96 1.28 1.856 3.52 2.176 4.928 2.688 4.16 1.408 8 3.136 12.16 4.8 3.456 1.472 7.04 2.496 10.496 4.416 2.88 1.536 6.656 1.6 9.92 2.944 1.28 0.64 1.92 1.792 3.328 1.92 3.2 0.192 6.208 1.152 8.96 2.688 0.896 0.64 1.856 1.664 2.944 1.92 3.2 0.64 6.4 1.408 9.536 2.688 2.88 1.216 5.952 2.56 9.024 2.944 1.728 0.256 2.048 1.92 3.712 1.92 2.816 0 5.12 1.856 7.808 2.432 3.648 0.832 7.232 2.176 10.816 3.264 0.832 0.256 2.176-0.128 2.56 0.32 2.176 2.88 5.824 1.408 8.768 2.88 3.2 1.6 7.296 2.368 11.008 3.584 3.264 1.216 6.528 2.432 10.048 2.56a2.56 2.56 0 0 1 1.6 0.512 26.24 26.24 0 0 0 7.808 2.944c1.28 0.256 2.56 0.512 3.84 0.64 2.368 0.384 4.224 1.984 6.528 2.112 3.328 0.192 6.208 2.24 9.728 2.368 1.28 0.064 3.52-0.128 4.608 0.96 2.24 2.24 5.504 2.112 8.064 2.752 5.504 1.472 11.456 1.152 17.152 3.008 3.328 1.152 7.04 0.768 10.688 1.6 5.824 1.28 12.16 0.896 18.432 1.28 11.52 0.704 23.04 0.448 34.432-0.896 2.944-0.384 5.632-0.512 8.512-1.28 4.096-1.024 8.256-1.664 12.416-1.984 3.584-0.32 6.912-1.856 10.56-1.472a1.344 1.344 0 0 0 0.704-0.256c3.2-1.92 6.848-1.92 10.112-3.072 2.368-0.704 5.12-0.64 7.04-1.792a20.096 20.096 0 0 1 6.72-2.56c3.648-0.64 6.208-3.648 9.92-3.392l0.448-0.256c2.496-1.92 5.568-2.944 8.32-3.84 3.072-1.088 6.016-2.304 8.896-3.712 3.84-1.92 7.424-4.416 11.136-6.592 1.152-0.64 2.752 0.32 3.52-1.472 0.64-1.472 1.984-2.176 3.712-2.56 1.92-0.512 4.096-1.28 4.864-3.456 0.384-0.896 1.088-1.024 1.856-0.896 1.024 0 1.92-0.512 2.432-1.408 2.368-3.52 6.4-5.12 9.6-7.488 3.392-2.304 6.016-5.44 9.536-7.552 4.16-2.432 7.168-6.336 10.496-9.664 2.88-2.944 5.568-5.952 8.576-8.576 2.368-2.048 3.456-4.864 5.76-6.912 1.664-1.408 4.224-2.88 4.864-5.12 1.024-3.392 3.84-4.8 6.144-6.912a13.312 13.312 0 0 1 3.584-4.608l0.896-1.408c1.216-0.704 0.576-2.56 1.92-3.328 2.304-1.536 3.2-4.416 4.224-6.592 1.6-3.392 3.52-6.656 5.44-9.792 2.048-3.456 3.328-7.296 5.888-10.496 1.92-2.496 3.136-5.76 4.48-8.704a369.92 369.92 0 0 1 7.808-15.104 130.56 130.56 0 0 0 4.48-9.088c1.408-3.2 3.2-6.208 4.736-9.28 1.472-3.2 3.52-6.144 4.672-9.28 1.472-3.904 3.648-7.168 5.312-10.88 0.512-1.344 0.384-2.944 1.28-3.904a18.688 18.688 0 0 0 4.16-7.168c1.472-3.84 4.736-6.912 5.12-11.2l0.256-0.128c1.472-1.152 1.984-2.944 2.816-4.48 1.28-2.432 1.92-5.248 3.52-7.488 2.496-3.52 3.904-7.424 5.824-11.2 1.28-2.304 2.176-4.928 3.52-7.04 2.176-3.456 3.648-7.36 5.76-10.752a43.776 43.776 0 0 0 3.584-7.68c0.64-1.792 1.92-3.52 3.008-5.12 1.984-3.072 3.392-6.592 5.952-9.344 1.088-1.152 1.024-2.752 2.304-4.096 2.24-2.176 3.392-5.12 5.12-7.68 1.28-1.92 2.24-4.224 3.776-5.696a47.232 47.232 0 0 0 7.04-9.344c1.984-3.2 4.544-6.016 6.848-8.704 3.712-4.48 7.68-8.96 11.84-13.44l11.136-11.776c2.176-2.304 1.6-2.816 0-4.416" fill="#133C9A"/>
      </svg>
    )
  }
  return null
}

function formatProviderName(provider: string): string {
  if (provider === "github") return "GitHub"
  if (provider === "google") return "Google"
  if (provider === "discord") return "Discord"
  return provider.charAt(0).toUpperCase() + provider.slice(1)
}

function OAuthBindingsSection({ user, onUnbind, onConnect }: { user: UserInfo; onUnbind: (provider: string) => void; onConnect: (provider: string) => void }) {
  const t = useTranslations("settings.account")
  const tc = useTranslations("common")
  const bindings = user.oauth_bindings ?? []
  const bindingMap = new Map(bindings.map((b) => [b.provider, b]))
  const hasPassword = user.has_password ?? false
  const canUnbindAny = hasPassword || bindings.length > 1

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-medium">{t("connectedAccountsTitle")}</h3>
        <p className="text-sm text-muted-foreground">
          {t("connectedAccountsDescription")}
        </p>
      </div>
      <div className="space-y-3 max-w-md">
        {SUPPORTED_PROVIDERS.map((provider) => {
          const binding = bindingMap.get(provider)
          const isBound = !!binding
          // Disable unbind when: no password AND this is the only binding left
          const unbindDisabled = !canUnbindAny

          return (
            <div
              key={provider}
              className="flex items-center gap-3 rounded-md border p-3"
            >
              <ProviderIcon provider={provider} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">
                    {formatProviderName(provider)}
                  </span>
                  {isBound ? (
                    <Badge
                      variant="secondary"
                      className="text-[10px] bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                    >
                      {t("connected")}
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="text-[10px] text-muted-foreground">
                      {t("notConnected")}
                    </Badge>
                  )}
                </div>
                {isBound && binding.email && (
                  <p className="text-xs text-muted-foreground truncate">
                    {binding.email}
                  </p>
                )}
                {isBound && binding.bound_at && (
                  <p className="text-xs text-muted-foreground">
                    {t("bound", { date: new Date(binding.bound_at).toLocaleDateString() })}
                  </p>
                )}
              </div>
              {isBound ? (
                unbindDisabled ? (
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="inline-flex">
                          <Button
                            variant="outline"
                            size="sm"
                            className="text-xs"
                            disabled
                          >
                            {t("disconnect")}
                          </Button>
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>
                        {t("disconnectTooltip")}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                ) : (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="outline" size="sm" className="text-xs">
                        {t("disconnect")}
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>
                          {t("disconnectConfirmTitle", { provider: formatProviderName(provider) })}
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                          {t("disconnectConfirmDescription", { provider: formatProviderName(provider) })}
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
                        <AlertDialogAction onClick={() => onUnbind(provider)}>
                          {t("disconnect")}
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                )
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  className="text-xs"
                  onClick={() => onConnect(provider)}
                >
                  {t("connect")}
                </Button>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function AccountSettings() {
  const { user, updateUser, logout } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const t = useTranslations("settings.account")

  const BIND_ERROR_MESSAGES: Record<string, string> = {
    email_mismatch: t("bindErrorEmailMismatch"),
    already_bound: t("bindErrorAlreadyBound"),
    already_connected: t("bindErrorAlreadyConnected"),
    feishu_email_required: t("bindErrorFeishuEmailRequired"),
  }

  const refreshUser = useCallback(async () => {
    try {
      const freshUser = await authApi.me()
      updateUser(freshUser)
    } catch {
      // Silently ignore — user data will be stale until next refresh
    }
  }, [updateUser])

  const _bindResultHandled = useRef(false)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (_bindResultHandled.current) return
    const bindSuccess = searchParams.get("bind_success")
    const bindError = searchParams.get("bind_error")

    if (!bindSuccess && !bindError) return
    _bindResultHandled.current = true

    if (bindSuccess) {
      const providerName = formatProviderName(bindSuccess)
      toast.success(t("providerConnected", { provider: providerName }))
      refreshUser()
      router.replace("/settings?tab=account", { scroll: false })
    } else if (bindError) {
      const text =
        BIND_ERROR_MESSAGES[bindError] ??
        t("bindErrorGeneric", { error: bindError })
      toast.error(text)
      router.replace("/settings?tab=account", { scroll: false })
    }
  }, [])

  const handleConnect = async (provider: string) => {
    const token = localStorage.getItem(ACCESS_TOKEN_KEY)
    if (!token) return
    try {
      // Fetch a one-time bind ticket instead of passing the raw JWT in the URL
      const res = await apiFetch<{ ticket: string }>("/api/auth/oauth/bind-ticket", {
        method: "POST",
      })
      window.location.href = `${getApiDirectUrl()}/api/auth/oauth/${provider}/authorize?action=bind&ticket=${res.ticket}`
    } catch {
      toast.error(t("connectFailed"))
    }
  }

  // SMTP status (default true to avoid flash-hiding the button)
  const [smtpConfigured, setSmtpConfigured] = useState(true)

  useEffect(() => {
    apiFetch<{ smtp_configured: boolean }>("/api/auth/registration-status")
      .then((data) => setSmtpConfigured(data.smtp_configured))
      .catch(() => {})
  }, [])

  // OAuth unbind state
  const [unbindingProvider, setUnbindingProvider] = useState<string | null>(null)

  const handleUnbind = async (provider: string) => {
    if (unbindingProvider) return

    setUnbindingProvider(provider)

    try {
      const updatedUser = await authApi.unbindOAuth(provider)
      updateUser({
        oauth_bindings: updatedUser.oauth_bindings,
        oauth_provider: updatedUser.oauth_provider,
        has_password: updatedUser.has_password,
      })
      toast.success(t("providerDisconnected", { provider: formatProviderName(provider) }))
    } catch (err) {
      if (err instanceof ApiError) {
        toast.error(err.message)
      } else {
        toast.error(t("disconnectFailed"))
      }
    } finally {
      setUnbindingProvider(null)
    }
  }

  // Password state (change password — for users who already have a password)
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  // Reset password via OTP state
  const locale = useLocale()
  const [resetMode, setResetMode] = useState(false)
  const [resetStep, setResetStep] = useState<"confirm" | "code">("confirm")
  const [resetCode, setResetCode] = useState("")
  const [resetNewPassword, setResetNewPassword] = useState("")
  const [resetConfirmPassword, setResetConfirmPassword] = useState("")
  const [resetSending, setResetSending] = useState(false)
  const [resetSubmitting, setResetSubmitting] = useState(false)
  const [resetResendCountdown, setResetResendCountdown] = useState(0)

  const hasPassword = user?.has_password ?? !user?.oauth_provider

  const confirmMismatch =
    confirmPassword.length > 0 && newPassword !== confirmPassword
  const newTooShort =
    newPassword.length > 0 && newPassword.length < MIN_PASSWORD_LENGTH
  const canSubmit =
    currentPassword.length > 0 &&
    newPassword.length >= MIN_PASSWORD_LENGTH &&
    newPassword === confirmPassword &&
    !saving

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return

    setSaving(true)
    setError("")

    try {
      await authApi.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      })
      setCurrentPassword("")
      setNewPassword("")
      setConfirmPassword("")
      toast.success(t("passwordChanged"))
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError(t("passwordChangeFailed"))
      }
    } finally {
      setSaving(false)
    }
  }

  // Resend countdown timer for reset OTP
  useEffect(() => {
    if (resetResendCountdown <= 0) return
    const timer = setTimeout(() => setResetResendCountdown(resetResendCountdown - 1), 1000)
    return () => clearTimeout(timer)
  }, [resetResendCountdown])

  const handleCancelReset = () => {
    setResetMode(false)
    setResetStep("confirm")
    setResetCode("")
    setResetNewPassword("")
    setResetConfirmPassword("")
  }

  const handleSendResetCode = async () => {
    setResetSending(true)
    try {
      await authApi.sendResetCode(locale)
      setResetStep("code")
      setResetResendCountdown(60)
      setResetCode("")
    } catch (err) {
      if (err instanceof ApiError) {
        toast.error(err.message)
      } else {
        toast.error(t("resetPasswordFailed"))
      }
    } finally {
      setResetSending(false)
    }
  }

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault()
    if (resetCode.length < 6 || resetNewPassword.length < MIN_PASSWORD_LENGTH || resetNewPassword !== resetConfirmPassword) return

    setResetSubmitting(true)
    try {
      await authApi.resetPassword({
        code: resetCode,
        new_password: resetNewPassword,
      })
      toast.success(t("passwordResetSuccess"))
      handleCancelReset()
    } catch (err) {
      if (err instanceof ApiError) {
        toast.error(err.message)
        // If code is invalid/expired, clear it so user can re-enter
        if (err.errorCode === "verification_code_invalid" || err.errorCode === "verification_code_expired" || err.errorCode === "verification_code_too_many_attempts") {
          setResetCode("")
        }
      } else {
        toast.error(t("resetPasswordFailed"))
      }
    } finally {
      setResetSubmitting(false)
    }
  }

  // Set password state (for OAuth-only users who have no password yet)
  const [setNewPw, setSetNewPw] = useState("")
  const [setConfirmPw, setSetConfirmPw] = useState("")
  const [setPwSaving, setSetPwSaving] = useState(false)
  const [setPwError, setSetPwError] = useState("")

  const setConfirmMismatch =
    setConfirmPw.length > 0 && setNewPw !== setConfirmPw
  const setNewTooShort =
    setNewPw.length > 0 && setNewPw.length < MIN_PASSWORD_LENGTH
  const canSetPw =
    setNewPw.length >= MIN_PASSWORD_LENGTH &&
    setNewPw === setConfirmPw &&
    !setPwSaving

  const handleSetPassword = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSetPw) return

    setSetPwSaving(true)
    setSetPwError("")

    try {
      const updatedUser = await authApi.setPassword({
        new_password: setNewPw,
      })
      updateUser({ has_password: updatedUser.has_password })
      setSetNewPw("")
      setSetConfirmPw("")
      toast.success(t("passwordSet"))
    } catch (err) {
      if (err instanceof ApiError) {
        setSetPwError(err.message)
      } else {
        setSetPwError(t("passwordSetFailed"))
      }
    } finally {
      setSetPwSaving(false)
    }
  }

  return (
    <div className="space-y-8">
      {/* Connected Accounts -- always shown for all users */}
      {user && (
        <OAuthBindingsSection
          user={user}
          onUnbind={handleUnbind}
          onConnect={handleConnect}
        />
      )}

      <Separator />

      {/* Email Section (read-only) */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-medium">{t("emailTitle")}</h3>
          <p className="text-sm text-muted-foreground">
            {t("emailDescription")}
          </p>
        </div>

        <div className="max-w-sm space-y-1">
          <p className="text-sm">{user?.email ?? ""}</p>
          <p className="text-xs text-muted-foreground">{t("emailReadOnly")}</p>
        </div>
      </div>

      <Separator />

      {/* Password Section */}
      {hasPassword ? (
        <div className="space-y-4">
          <div>
            <h3 className="text-base font-medium">{t("changePasswordTitle")}</h3>
            <p className="text-sm text-muted-foreground">
              {t("changePasswordDescription")}
            </p>
          </div>

          {!resetMode ? (
            /* Normal change password form */
            <form onSubmit={handleSubmit} className="space-y-4 max-w-sm">
              <div className="space-y-2">
                <label className="text-sm font-medium">{t("currentPasswordLabel")} <span className="text-destructive">*</span></label>
                <Input
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  placeholder={t("currentPasswordPlaceholder")}
                />
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium">{t("newPasswordLabel")} <span className="text-destructive">*</span></label>
                <Input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder={t("newPasswordPlaceholder")}
                />
                {newTooShort && (
                  <p className="text-xs text-destructive">
                    {t("passwordMinLength", { min: MIN_PASSWORD_LENGTH })}
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium">{t("confirmPasswordLabel")} <span className="text-destructive">*</span></label>
                <Input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder={t("confirmPasswordPlaceholder")}
                />
                {confirmMismatch && (
                  <p className="text-xs text-destructive">
                    {t("passwordMismatch")}
                  </p>
                )}
              </div>

              {error && (
                <p className="text-sm text-destructive">{error}</p>
              )}

              <div className="flex items-center gap-4">
                <Button type="submit" size="sm" disabled={!canSubmit}>
                  {saving ? t("changing") : t("changePassword")}
                </Button>
              </div>

              {smtpConfigured && (
                <button
                  type="button"
                  className="text-sm text-muted-foreground hover:text-foreground underline underline-offset-4 transition-colors"
                  onClick={() => setResetMode(true)}
                >
                  {t("forgotPassword")}
                </button>
              )}
            </form>
          ) : (
            /* OTP Reset flow */
            <div className="space-y-4 max-w-sm">
              {resetStep === "confirm" && (
                <div className="space-y-4">
                  <div className="flex items-center gap-3">
                    <Button size="sm" onClick={handleSendResetCode} disabled={resetSending}>
                      {resetSending ? t("sendingResetCode") : t("sendResetCode")}
                    </Button>
                    <button
                      type="button"
                      className="text-sm text-muted-foreground hover:text-foreground underline underline-offset-4 transition-colors"
                      onClick={handleCancelReset}
                    >
                      {t("cancelReset")}
                    </button>
                  </div>
                </div>
              )}

              {resetStep === "code" && (
                <form onSubmit={handleResetPassword} className="space-y-4">
                  <p className="text-sm text-muted-foreground">
                    {t("resetCodeSent", { email: user?.email ?? "" })}
                  </p>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">{t("enterResetCode")} <span className="text-destructive">*</span></label>
                    <InputOTP
                      maxLength={6}
                      value={resetCode}
                      onChange={setResetCode}
                    >
                      <InputOTPGroup>
                        <InputOTPSlot index={0} />
                        <InputOTPSlot index={1} />
                        <InputOTPSlot index={2} />
                      </InputOTPGroup>
                      <InputOTPSeparator />
                      <InputOTPGroup>
                        <InputOTPSlot index={3} />
                        <InputOTPSlot index={4} />
                        <InputOTPSlot index={5} />
                      </InputOTPGroup>
                    </InputOTP>
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">{t("newPasswordLabel")} <span className="text-destructive">*</span></label>
                    <Input
                      type="password"
                      value={resetNewPassword}
                      onChange={(e) => setResetNewPassword(e.target.value)}
                      placeholder={t("newPasswordPlaceholder")}
                    />
                    {resetNewPassword.length > 0 && resetNewPassword.length < MIN_PASSWORD_LENGTH && (
                      <p className="text-xs text-destructive">
                        {t("passwordMinLength", { min: MIN_PASSWORD_LENGTH })}
                      </p>
                    )}
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">{t("confirmPasswordLabel")} <span className="text-destructive">*</span></label>
                    <Input
                      type="password"
                      value={resetConfirmPassword}
                      onChange={(e) => setResetConfirmPassword(e.target.value)}
                      placeholder={t("confirmPasswordPlaceholder")}
                    />
                    {resetConfirmPassword.length > 0 && resetNewPassword !== resetConfirmPassword && (
                      <p className="text-xs text-destructive">
                        {t("passwordMismatch")}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-3">
                    <Button
                      type="submit"
                      size="sm"
                      disabled={
                        resetCode.length < 6 ||
                        resetNewPassword.length < MIN_PASSWORD_LENGTH ||
                        resetNewPassword !== resetConfirmPassword ||
                        resetSubmitting
                      }
                    >
                      {resetSubmitting ? t("resettingPassword") : t("resetPassword")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={handleSendResetCode}
                      disabled={resetResendCountdown > 0 || resetSending}
                    >
                      {resetSending
                        ? t("sendingResetCode")
                        : resetResendCountdown > 0
                          ? t("resendCodeIn", { seconds: resetResendCountdown })
                          : t("resendCode")}
                    </Button>
                  </div>
                  <button
                    type="button"
                    className="text-sm text-muted-foreground hover:text-foreground underline underline-offset-4 transition-colors"
                    onClick={handleCancelReset}
                  >
                    {t("cancelReset")}
                  </button>
                </form>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <div>
            <h3 className="text-base font-medium">{t("setPasswordTitle")}</h3>
            <p className="text-sm text-muted-foreground">
              {t("setPasswordDescription")}
            </p>
          </div>

          <form onSubmit={handleSetPassword} className="space-y-4 max-w-sm">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t("newPasswordLabel")} <span className="text-destructive">*</span></label>
              <Input
                type="password"
                value={setNewPw}
                onChange={(e) => setSetNewPw(e.target.value)}
                placeholder={t("newPasswordPlaceholder")}
              />
              {setNewTooShort && (
                <p className="text-xs text-destructive">
                  {t("passwordMinLength", { min: MIN_PASSWORD_LENGTH })}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">{t("confirmSetPasswordLabel")} <span className="text-destructive">*</span></label>
              <Input
                type="password"
                value={setConfirmPw}
                onChange={(e) => setSetConfirmPw(e.target.value)}
                placeholder={t("confirmSetPasswordPlaceholder")}
              />
              {setConfirmMismatch && (
                <p className="text-xs text-destructive">
                  {t("passwordMismatch")}
                </p>
              )}
            </div>

            {setPwError && (
              <p className="text-sm text-destructive">{setPwError}</p>
            )}

            <Button type="submit" size="sm" disabled={!canSetPw}>
              {setPwSaving ? t("setting") : t("setPassword")}
            </Button>
          </form>
        </div>
      )}

      <Separator />

      {/* Danger Zone */}
      <DeleteAccountSection t={t} user={user} logout={logout} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Delete Account Section
// ---------------------------------------------------------------------------

const COUNTDOWN_SECONDS = 5

function DeleteAccountSection({
  t,
  user,
  logout,
}: {
  t: ReturnType<typeof useTranslations<"settings.account">>
  user: UserInfo | null
  logout: () => void
}) {
  const tc = useTranslations("common")
  const [open, setOpen] = useState(false)
  const [confirmInput, setConfirmInput] = useState("")
  const [countdown, setCountdown] = useState(0)
  const [deleting, setDeleting] = useState(false)

  const target = user?.username || user?.email || ""
  const inputMatched = confirmInput === target

  // Start countdown when input matches
  useEffect(() => {
    if (!inputMatched || !open) {
      setCountdown(0)
      return
    }
    setCountdown(COUNTDOWN_SECONDS)
    const timer = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(timer)
          return 0
        }
        return prev - 1
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [inputMatched, open])

  // Reset state when dialog closes
  useEffect(() => {
    if (!open) {
      setConfirmInput("")
      setCountdown(0)
      setDeleting(false)
    }
  }, [open])

  const handleDelete = async () => {
    if (!inputMatched || countdown > 0 || deleting) return
    setDeleting(true)
    try {
      await authApi.deleteAccount()
      toast.success(t("deleteAccountFinal"))
      logout()
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err)
      toast.error(msg)
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-medium text-destructive">
          {t("dangerZoneTitle")}
        </h3>
        <p className="text-sm text-muted-foreground">
          {t("dangerZoneDescription")}
        </p>
      </div>

      <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 flex items-center justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          {t("deleteAccountDesc")}
        </p>
        <AlertDialog open={open} onOpenChange={setOpen}>
          <AlertDialogTrigger asChild>
            <Button variant="destructive" size="sm" className="shrink-0">
              {t("deleteAccountButton")}
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t("deleteAccountConfirmTitle")}</AlertDialogTitle>
              <AlertDialogDescription asChild>
                <div className="text-sm text-muted-foreground">
                  <p>{t("deleteAccountConfirmDesc")}</p>
                  <ul className="mt-2 list-disc pl-5 space-y-1">
                    <li>{t("deleteAccountConfirmItem1")}</li>
                    <li>{t("deleteAccountConfirmItem2")}</li>
                    <li>{t("deleteAccountConfirmItem3")}</li>
                    <li>{t("deleteAccountConfirmItem4")}</li>
                    <li>{t("deleteAccountConfirmItem5")}</li>
                  </ul>
                </div>
              </AlertDialogDescription>
            </AlertDialogHeader>

            <div className="space-y-2 pt-2">
              <label className="text-sm font-medium">
                {t("deleteAccountInputHint", { target })}
              </label>
              <Input
                value={confirmInput}
                onChange={(e) => setConfirmInput(e.target.value)}
                placeholder={target}
                autoComplete="off"
              />
            </div>

            <AlertDialogFooter>
              <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
              <Button
                variant="destructive"
                disabled={!inputMatched || countdown > 0 || deleting}
                onClick={handleDelete}
              >
                {deleting
                  ? t("deleteAccountDeleting")
                  : countdown > 0
                    ? t("deleteAccountCountdown", { seconds: String(countdown) })
                    : t("deleteAccountFinal")}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </div>
  )
}
