import xlsxwriter
from datetime import datetime, date
import os

from django.http import HttpResponseRedirect, Http404, HttpResponse
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.core.urlresolvers import reverse
from django.conf import settings
from django.views.generic import View, CreateView

from .forms import TransitionalDonationsFileUploadForm, DateRangeSelector, PledgeForm
from .models import BankTransaction, PartnerCharity
from pinpayments.models import PinTransaction
from paypal.standard.forms import PayPalPaymentsForm


@login_required()
def upload_donations_file(request):
    if request.method == 'POST':
        form = TransitionalDonationsFileUploadForm(request.POST, request.FILES)
        if form.is_valid():
            # file is saved
            form.save()
            return HttpResponseRedirect('/admin/donation/pledge/')
    else:
        form = TransitionalDonationsFileUploadForm()
    return render(request, 'transitional_upload_form.html', {'form': form})


@login_required()
def accounting_reconciliation(request):
    if request.method == 'POST':
        form = DateRangeSelector(request.POST)
        if not form.is_valid():
            return HttpResponseRedirect(reverse('accounting-reconciliation'))
    else:
        form = DateRangeSelector()

    # There's gotta be a better way to do this
    if hasattr(form, 'cleaned_data'):
        start = form.cleaned_data['start']
        end = form.cleaned_data['end']
    else:
        start = form.fields['start'].initial
        end = form.fields['end'].initial

    totals = {partner.name: BankTransaction.objects
                .filter(date__gte=start, date__lte=end, pledge__recipient_org=partner)
                .aggregate(Sum('amount'))['amount__sum']
              for partner in PartnerCharity.objects.all().order_by('name')}

    # This shouldn't/can't happen but it will mess up the reconciliation so let's check.
    if BankTransaction.objects.filter(pledge__isnull=False, do_not_reconcile=True).exists():
        raise Exception("Error: transaction reconciled to pledge and also marked 'Do not reconcile'")

    exceptions = BankTransaction.objects.filter(date__gte=start, date__lte=end).exclude(pledge__isnull=False).order_by('date')

    return render(request, 'reconciliation.html', {'form': form,
                                                   'totals': sorted(totals.iteritems()),
                                                   'grand_total': sum(filter(None, totals.values())),
                                                   'exceptions': exceptions})


@login_required()
def download_transactions(request):
    # TODO We don't pass parameters to this function yet.
    # Write some javascript to restrict dates. Maybe easiest to switch out the date widgets for the jQuery UI ones.
    if request.method != 'GET':
        raise Http404

    try:
        start = datetime.strptime(request.GET['start'], '%Y-%m-%d').date()
    except (KeyError, ValueError):
        start = date(2015, 1, 1)

    try:
        end = datetime.strptime(request.GET['end'], '%Y-%m-%d').date()
    except (KeyError, ValueError):
        end = date.today()

    path = os.path.join(settings.MEDIA_ROOT, 'downloads')
    filename = 'EAA donations {0} to {1} downloaded {2}.xlsx'.format(start, end, datetime.now())
    with xlsxwriter.Workbook(os.path.join(path, filename)) as wb:
        ws = wb.add_worksheet()
        date_format = wb.add_format({'num_format': 'dd mmm yyyy'})
        from collections import OrderedDict
        template = OrderedDict([
            ('Date', 'date'),
            ('Amount', 'amount'),
            ('EAA Reference', 'reference'),
            ('First Name', 'pledge__first_name'),
            ('Last Name', 'pledge__last_name'),
            ('Email', 'pledge__email'),
            ('Subscribe to marketing updates', 'pledge__subscribe_to_updates'),
            ('Can publish donation', 'pledge__publish_donation'),
            ('Designation', 'pledge__recipient_org__name')
        ])
        ws.write_row(0, 0, template.keys())
        row = 0
        for bt_row in BankTransaction.objects.\
                filter(date__gte=start, date__lte=end, do_not_reconcile=False).\
                order_by('date').\
                values_list(*template.values()):
            row += 1
            ws.write_datetime(row, 0, bt_row[0], date_format)
            ws.write_row(row, 1, bt_row[1:])

    response = HttpResponse(open(os.path.join(path, filename)).read(),
                            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="{}"'.format(filename)
    return response


# @login_required()
class PledgeView(View):

    def post(self, request):
        form = PledgeForm(request.POST)

        if form.is_valid():
            form.save()
        else:
            return Http404

        if int(request.POST.get('payment_method')) == 1:
            transaction = PinTransaction()
            transaction.card_token = request.POST.get('card_token')
            transaction.ip_address = request.POST.get('ip_address')
            transaction.amount = form.cleaned_data['amount']  # Amount in dollars. Define with your own business logic.
            transaction.currency = 'AUD'  # Pin supports AUD and USD. Fees apply for currency conversion.
            transaction.description = 'Payment for invoice #12508'  # Define with your own business logic
            transaction.email_address = request.POST.get('email')
            transaction.pledge = form.instance
            transaction.save()
            result = transaction.process_transaction()  # Typically "Success" or an error message
        # if transaction.succeeded:
        #     return "We got the money!"
        # else:
        #     return "No money today :( Error message: %s " % result

        return HttpResponseRedirect('/admin/donation/pledge/')

    def get(self, request):
        paypal_dict = {
            "business": "placeholder@example.com",
            "amount": "0",
            "item_name": "Donation",
            "notify_url": "https://www.example.com" + reverse('paypal-ipn'),
            "return_url": "https://www.example.com/your-return-location/",
            "cancel_return": "https://www.example.com/your-cancel-location/",
        }
        paypal_form = PayPalPaymentsForm(button_type='donate', initial=paypal_dict)
        form = PledgeForm()
        return render(request, 'pledge.html', {'form': form, 'paypalform': paypal_form}) # , 'org': org


# class Paypal(View):
#     def get(self, request):
#         # What you want the button to do.
#         paypal_dict = {
#             "business": "receiver_email@example.com",
#             "amount": "10000000.00",
#             "item_name": "name of the item",
#             "invoice": "unique-invoice-id",
#             "notify_url": "https://www.example.com" + reverse('paypal-ipn'),
#             "return_url": "https://www.example.com/your-return-location/",
#             "cancel_return": "https://www.example.com/your-cancel-location/",
#             "custom": "Upgrade all users!",  # Custom command to correlate to some function later (optional)
#         }
#
#         # Create the instance.
#         paypal_form = PayPalPaymentsForm(initial=paypal_dict)
#         context = {"form": paypal_form}
#         return render(request, "payment.html", context)

