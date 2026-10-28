// 404 inside the shell.
import { Link } from 'react-router'
import { Compass } from 'lucide-react'
import { EmptyState } from '@/components/pf/EmptyState'

export default function NotFoundPage() {
  return (
    <EmptyState
      icon={Compass}
      title="Такой страницы нет"
      text="Ссылка устарела или в ней опечатка."
      action={<Link to="/" className="text-small font-medium text-primary hover:underline">На главную</Link>}
      className="py-24"
    />
  )
}
