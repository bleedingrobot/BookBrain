import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

export function useScanStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['scan', jobId],
    queryFn: () => api.getScanStatus(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1000 : false),
  })
}
