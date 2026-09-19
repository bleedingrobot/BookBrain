import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Header } from './components/Header'
import { Sidebar } from './components/Sidebar'
import { Acquire } from './pages/Acquire'
import { Activity } from './pages/Activity'
import { Dashboard } from './pages/Dashboard'
import { Duplicates } from './pages/Duplicates'
import { Inbox } from './pages/Inbox'
import { Library } from './pages/Library'
import { LibraryAudit } from './pages/LibraryAudit'
import { ReviewQueue } from './pages/ReviewQueue'
import { Settings } from './pages/Settings'
import { SmartCollections } from './pages/SmartCollections'
import { Wishlist } from './pages/Wishlist'

const queryClient = new QueryClient()

export default function App() {
  const [drawerOpen, setDrawerOpen] = useState(false)

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="lg:flex lg:items-start">
          <Sidebar open={drawerOpen} onClose={() => setDrawerOpen(false)} />
          <div className="min-w-0 flex-1">
            <div className="mx-auto max-w-6xl px-4 pb-10 sm:px-6">
              <Header onOpenDrawer={() => setDrawerOpen(true)} />
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/inbox" element={<Inbox />} />
                <Route path="/review" element={<ReviewQueue />} />
                <Route path="/library" element={<Library />} />
                <Route path="/wishlist" element={<Wishlist />} />
                <Route path="/smart-collections" element={<SmartCollections />} />
                <Route path="/acquire" element={<Acquire />} />
                <Route path="/library-audit" element={<LibraryAudit />} />
                <Route path="/duplicates" element={<Duplicates />} />
                <Route path="/activity" element={<Activity />} />
                <Route path="/settings" element={<Settings />} />
              </Routes>
            </div>
          </div>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
