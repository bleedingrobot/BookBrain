import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { NavBar } from './components/NavBar'
import { Activity } from './pages/Activity'
import { Dashboard } from './pages/Dashboard'
import { Duplicates } from './pages/Duplicates'
import { Inbox } from './pages/Inbox'
import { Library } from './pages/Library'
import { LibraryAudit } from './pages/LibraryAudit'
import { ReviewQueue } from './pages/ReviewQueue'
import { Settings } from './pages/Settings'
import { Wishlist } from './pages/Wishlist'

const queryClient = new QueryClient()

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <NavBar />
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/inbox" element={<Inbox />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/library" element={<Library />} />
          <Route path="/wishlist" element={<Wishlist />} />
          <Route path="/library-audit" element={<LibraryAudit />} />
          <Route path="/duplicates" element={<Duplicates />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
