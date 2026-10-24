import { Navigate, Route, Routes } from "react-router-dom";
import { Layout, RequireAdmin, RequireAuth } from "./components/Layout";
import Home from "./pages/Home";
import PropertyPage from "./pages/PropertyPage";
import SearchPage from "./pages/Search";
import LegalPage from "./pages/Legal";
import AccountPage, { SecurityPage } from "./pages/account/Account";
import { AccountLayout } from "./components/AccountLayout";
import { TripDetailPage, TripsPage } from "./pages/account/Trips";
import { PayPage } from "./pages/account/Pay";
import { InvoicePage } from "./pages/account/Invoice";
import { AdminBookingsPage } from "./pages/admin/AdminBookingsPage";
import { AdminBookingDetailPage } from "./pages/admin/AdminBookingDetailPage";
import { AdminCalendarPage } from "./pages/admin/AdminCalendarPage";
import { AdminCancellationPage } from "./pages/admin/AdminCancellationPage";
import { AdminDashboardPage } from "./pages/admin/AdminDashboardPage";
import { AdminRevenuePage } from "./pages/admin/AdminRevenuePage";
import { AdminExportsPage, AdminPaymentsPage, AdminPayoutsPage, AdminWebhooksPage } from "./pages/admin/AdminMoneyPages";
import { AdminIssuesPage, AdminReviewsPage } from "./pages/admin/AdminReviewsIssuesPages";
import { AdminUserDetailPage, AdminUsersPage } from "./pages/admin/AdminUsersPages";
import { MoneyPage } from "./pages/host/MoneyPage";
import { AdminRefundsPage } from "./pages/admin/AdminRefundsPage";
import { RequestsPage } from "./pages/host/RequestsPage";
import { ScrollToTop } from "./components/ScrollToTop";
import { AdminLayout } from "./components/AdminShell";
import { HostLayout } from "./components/HostShell";
import { AuditPage, LegalAdminPage, SettingsPage } from "./pages/admin/AdminPages";
import { HostDetailPage, HostsListPage } from "./pages/admin/AdminHostPages";
import { TaxRulesPage } from "./pages/admin/AdminTaxPage";
import { CataloguesPage, ListingDetailPage, ListingsPage } from "./pages/admin/AdminListingPages";
import { ApplyPage, HostHome, PayoutsPage } from "./pages/host/HostPages";
import { PropertiesPage, PropertyEditor, UnitEditor } from "./pages/host/PropertyPages";
import { ForgotPasswordPage, LoginPage, RegisterPage, ResetPasswordPage, VerifyEmailPage } from "./pages/auth/AuthPages";

export default function App() {
  return (
    <>
    <ScrollToTop />
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="login" element={<LoginPage />} />
        <Route path="register" element={<RegisterPage />} />
        <Route path="verify" element={<VerifyEmailPage />} />
        <Route path="forgot-password" element={<ForgotPasswordPage />} />
        <Route path="reset-password" element={<ResetPasswordPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="property/:propertyId" element={<PropertyPage />} />
        <Route path="legal/:docType" element={<LegalPage />} />

        <Route element={<RequireAuth />}>
          <Route path="account" element={<AccountLayout />}>
            <Route index element={<AccountPage />} />
            <Route path="security" element={<SecurityPage />} />
          </Route>
          <Route path="account/trips" element={<TripsPage />} />
          <Route path="account/trips/:bookingId" element={<TripDetailPage />} />
          <Route path="account/trips/:bookingId/pay" element={<PayPage />} />
          <Route path="account/trips/:bookingId/invoice" element={<InvoicePage />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>

      <Route element={<RequireAuth />}>
        <Route path="host" element={<HostLayout />}>
          <Route index element={<HostHome />} />
          <Route path="apply" element={<ApplyPage />} />
          <Route path="payouts" element={<PayoutsPage />} />
          <Route path="money" element={<MoneyPage />} />
          <Route path="requests" element={<RequestsPage />} />
          <Route path="properties" element={<PropertiesPage />} />
          <Route path="properties/:propertyId" element={<PropertyEditor />} />
          <Route path="properties/:propertyId/units/:unitId" element={<UnitEditor />} />
          <Route path="*" element={<Navigate to="/host" replace />} />
        </Route>
      </Route>

      <Route element={<RequireAdmin />}>
        <Route path="admin" element={<AdminLayout />}>
          <Route index element={<AdminDashboardPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="hosts" element={<HostsListPage />} />
          <Route path="hosts/:hostId" element={<HostDetailPage />} />
          <Route path="bookings" element={<AdminBookingsPage />} />
          <Route path="bookings/:bookingId" element={<AdminBookingDetailPage />} />
          <Route path="users" element={<AdminUsersPage />} />
          <Route path="users/:userId" element={<AdminUserDetailPage />} />
          <Route path="revenue" element={<AdminRevenuePage />} />
          <Route path="payments" element={<AdminPaymentsPage />} />
          <Route path="payouts" element={<AdminPayoutsPage />} />
          <Route path="calendar" element={<AdminCalendarPage />} />
          <Route path="reviews" element={<AdminReviewsPage />} />
          <Route path="issues" element={<AdminIssuesPage />} />
          <Route path="webhooks" element={<AdminWebhooksPage />} />
          <Route path="exports" element={<AdminExportsPage />} />
          <Route path="refunds" element={<AdminRefundsPage />} />
          <Route path="cancellation" element={<AdminCancellationPage />} />
          <Route path="listings" element={<ListingsPage />} />
          <Route path="listings/:propertyId" element={<ListingDetailPage />} />
          <Route path="catalogues" element={<CataloguesPage />} />
          <Route path="tax" element={<TaxRulesPage />} />
          <Route path="legal" element={<LegalAdminPage />} />
          <Route path="audit" element={<AuditPage />} />
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Route>
      </Route>
    </Routes>
    </>
  );
}
