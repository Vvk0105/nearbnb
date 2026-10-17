import { Navigate, Route, Routes } from "react-router-dom";
import { Layout, RequireAdmin, RequireAuth } from "./components/Layout";
import Home from "./pages/Home";
import PropertyPage from "./pages/PropertyPage";
import SearchPage from "./pages/Search";
import LegalPage from "./pages/Legal";
import AccountPage from "./pages/account/Account";
import { TripDetailPage, TripsPage } from "./pages/account/Trips";
import { AdminBookingsPage } from "./pages/admin/AdminBookingsPage";
import { RequestsPage } from "./pages/host/RequestsPage";
import { AdminLayout, AuditPage, LegalAdminPage, SettingsPage } from "./pages/admin/AdminPages";
import { HostDetailPage, HostsListPage } from "./pages/admin/AdminHostPages";
import { TaxRulesPage } from "./pages/admin/AdminTaxPage";
import { CataloguesPage, ListingDetailPage, ListingsPage } from "./pages/admin/AdminListingPages";
import { ApplyPage, HostHome, PayoutsPage } from "./pages/host/HostPages";
import { PropertiesPage, PropertyEditor, UnitEditor } from "./pages/host/PropertyPages";
import { ForgotPasswordPage, LoginPage, RegisterPage, ResetPasswordPage, VerifyEmailPage } from "./pages/auth/AuthPages";

export default function App() {
  return (
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
          <Route path="account" element={<AccountPage />} />
          <Route path="account/trips" element={<TripsPage />} />
          <Route path="account/trips/:bookingId" element={<TripDetailPage />} />
          <Route path="host" element={<HostHome />} />
          <Route path="host/apply" element={<ApplyPage />} />
          <Route path="host/payouts" element={<PayoutsPage />} />
          <Route path="host/requests" element={<RequestsPage />} />
          <Route path="host/properties" element={<PropertiesPage />} />
          <Route path="host/properties/:propertyId" element={<PropertyEditor />} />
          <Route path="host/properties/:propertyId/units/:unitId" element={<UnitEditor />} />
        </Route>

        <Route element={<RequireAdmin />}>
          <Route path="admin" element={<AdminLayout />}>
            <Route index element={<Navigate to="settings" replace />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="hosts" element={<HostsListPage />} />
            <Route path="hosts/:hostId" element={<HostDetailPage />} />
            <Route path="bookings" element={<AdminBookingsPage />} />
            <Route path="listings" element={<ListingsPage />} />
            <Route path="listings/:propertyId" element={<ListingDetailPage />} />
            <Route path="catalogues" element={<CataloguesPage />} />
            <Route path="tax" element={<TaxRulesPage />} />
            <Route path="legal" element={<LegalAdminPage />} />
            <Route path="audit" element={<AuditPage />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
